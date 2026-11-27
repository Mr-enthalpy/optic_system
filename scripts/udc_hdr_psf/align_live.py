"""Live alignment view while the mount is being repositioned.

The phone cannot stream raw frames to the host, so the update rate is the
capture rate: one short exposure per update.  A full session capture writes a
whole run -- DNG, plane binaries, tar export, full-frame float64 means -- which
is right for an acquisition and far too slow for a view.  This tool therefore
asks the probe app for one short run and pulls **only the raw plane of that
run** through ``adb exec-out``, decoding it in memory.  Nothing is exported,
nothing is averaged over the frame, and no DNG is re-read.

What the view answers, every update:

* where the PSF centre is, against the pixel the operator is aiming for, in the
  same binned coordinates the session uses, with two centroid radii so a
  radius-dependent centre is visible rather than hidden;
* the accelerometer roll and tilt, plus the roll relative to the P1 and P2
  states;
* two images: the **whole frame**, shrunk, so a spot that has walked out of the
  working area is still visible instead of vanishing at a crop edge; and a
  full-resolution zoom on the measured centre, so the core can be judged
  directly.

Serving: ``--serve`` publishes a small page over HTTP, so any machine on the
network can open it.  A capture host reached over ssh runs in session 0, where
a GUI window would be invisible, so the page is the portable choice.

A frame from this tool is a control aid, not an acquisition: it is labelled as
such in the run's own note and is not part of the session data.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import read_tilt  # noqa: E402
import rotation_analysis as ra  # noqa: E402

REFINE_HALF = 48        # centre-fit patch, big enough for the larger centroid
FRAME_MARGIN = 12       # ignore the outermost pixels when looking for the spot
RADII_PX = (5.0, 10.0)  # 5 px is the session convention; 10 px is the cross-check
TRAIL_LEN = 40          # remembered centres, drawn on the overview

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>alignment</title>
<style>
 body{background:#0d1117;color:#e6edf3;font:14px/1.45 "Segoe UI",system-ui,sans-serif;margin:0;padding:14px}
 .row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
 .panel{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px 12px}
 .big{font-size:30px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}
 .ok{color:#3fb950}.warn{color:#d29922}.bad{color:#f85149}.dim{color:#8b949e}
 .note{font-size:15px;margin-top:6px}
 table{border-collapse:collapse}
 td{padding:1px 12px 2px 0;font-variant-numeric:tabular-nums;white-space:nowrap}
 canvas{image-rendering:pixelated;border:1px solid #30363d;border-radius:4px;display:block}
 #bar{height:6px;background:#21262d;border-radius:3px;overflow:hidden;width:100%}
 #fill{height:100%;background:#3fb950;width:0;transition:width .2s}
 .cap{font-size:12px;margin-top:5px}
</style></head><body>
<div class="row">
  <div class="panel" style="flex:0 0 260px">
    <div class="big" id="dx">--</div>
    <div class="big" id="dy">--</div>
    <div id="verdict" class="note dim">waiting for the first capture</div>
    <div id="bar" style="margin-top:9px"><div id="fill"></div></div>
    <div class="dim" style="margin-top:8px;font-size:12px" id="when">--</div>
  </div>
  <div class="panel">
    <canvas id="overview" width="400" height="300"></canvas>
    <div class="cap dim" id="ovcap">whole frame: orange = target, cyan = centre, dots = last centres</div>
  </div>
  <div class="panel">
    <canvas id="zoom" width="300" height="300"></canvas>
    <div class="cap dim" id="zcap">zoom on the centre (full resolution)</div>
  </div>
  <div class="panel" style="flex:1 1 240px">
    <table>
      <tr><td class="dim">roll</td><td class="big" id="roll">--</td></tr>
      <tr><td class="dim">tilt</td><td id="tilt">--</td></tr>
      <tr><td class="dim">&Delta; vs P1 target</td><td id="dp1">--</td></tr>
      <tr><td class="dim">&Delta; vs P2</td><td id="dp2">--</td></tr>
      <tr><td class="dim">centre r5 px</td><td id="c5">--</td></tr>
      <tr><td class="dim">centre r10 px</td><td id="c10">--</td></tr>
      <tr><td class="dim">target</td><td id="target">--</td></tr>
      <tr><td class="dim">peak DN</td><td id="peak">--</td></tr>
      <tr><td class="dim">frame</td><td id="frame">--</td></tr>
      <tr><td class="dim">cycle</td><td id="cyc">--</td></tr>
    </table>
    <div class="dim" style="margin-top:10px;font-size:12px">last 10</div>
    <table id="hist"></table>
  </div>
</div>
<script>
const f=(v,d)=> (v===null||v===undefined)?'--':((v>=0?'+':'')+v.toFixed(d));
function paint(cv, patch, scale){
  if(!patch||!patch.length) return;
  const img=document.createElement('canvas');
  img.width=patch[0].length; img.height=patch.length;
  const ic=img.getContext('2d'), id=ic.createImageData(img.width,img.height);
  const s=Math.asinh(Math.max(1,scale));
  for(let y=0;y<img.height;y++)for(let x=0;x<img.width;x++){
    const t=Math.asinh(Math.max(0,patch[y][x]))/s;
    const k=(y*img.width+x)*4, g=Math.min(255,Math.max(0,t*255));
    id.data[k]=g*0.5; id.data[k+1]=g*0.88; id.data[k+2]=g*0.95; id.data[k+3]=255;
  }
  ic.putImageData(id,0,0);
  const ctx=cv.getContext('2d');
  ctx.imageSmoothingEnabled=false;
  ctx.fillStyle='#0d1117'; ctx.fillRect(0,0,cv.width,cv.height);
  ctx.drawImage(img,0,0,cv.width,cv.height);
}
function cross(ctx,cx,cy,r,colour,dash){
  ctx.strokeStyle=colour; ctx.lineWidth=1; ctx.setLineDash(dash||[]);
  ctx.beginPath(); ctx.moveTo(cx-r,cy); ctx.lineTo(cx+r,cy);
  ctx.moveTo(cx,cy-r); ctx.lineTo(cx,cy+r); ctx.stroke(); ctx.setLineDash([]);
}
function ring(ctx,cx,cy,r,colour,w){
  ctx.strokeStyle=colour; ctx.lineWidth=w; ctx.beginPath(); ctx.arc(cx,cy,r,0,2*Math.PI); ctx.stroke();
}
function draw(s){
  document.getElementById('when').textContent=(s.utc||'--')+'   #'+(s.index??'--');
  document.getElementById('cyc').textContent=(s.seconds??'--')+' s';
  document.getElementById('roll').textContent=f(s.roll_deg,3);
  document.getElementById('tilt').textContent=f(s.tilt_deg,2);
  document.getElementById('dp1').textContent=f(s.delta_p1_deg,2)+' deg';
  document.getElementById('dp2').textContent=f(s.delta_p2_deg,2)+' deg';
  document.getElementById('target').textContent=s.target_xy?('('+s.target_xy[0].toFixed(1)+', '+s.target_xy[1].toFixed(1)+')'):'--';
  document.getElementById('peak').textContent=(s.peak_dn===null||s.peak_dn===undefined)?'--':
    (s.peak_dn.toFixed(0)+' DN'+(s.black_dn?' (black '+s.black_dn.toFixed(0)+')':''));
  const fsh=s.frame_shape||[0,0];
  document.getElementById('frame').textContent=fsh[0]+'x'+fsh[1];
  const v=document.getElementById('verdict');
  if(!s.ok){v.className='note bad';v.textContent=s.error||'capture failed';
    document.getElementById('dx').textContent='--';document.getElementById('dy').textContent='--';return;}
  const dx=s.delta_xy[0], dy=s.delta_xy[1];
  const found=s.spot_found;
  // The session re-locates the centre in every bracket, so the spot does not
  // have to sit on a pixel: it has to be inside the working box, off the frame
  // border and not clipped.
  const tol=s.tolerance_px||150;
  const grade=a=>Math.abs(a)<=tol?'ok':(Math.abs(a)<=tol*2?'warn':'bad');
  const ex=document.getElementById('dx'), ey=document.getElementById('dy');
  if(found){
    ex.textContent='dX '+f(dx,1)+' px'; ex.className='big '+grade(dx);
    ey.textContent='dY '+f(dy,1)+' px'; ey.className='big '+grade(dy);
  }else{
    ex.textContent='dX --'; ex.className='big dim';
    ey.textContent='dY --'; ey.className='big dim';
  }
  // Without a spot these are fits to noise, so they are not shown as centres.
  document.getElementById('c5').textContent=(found&&s.centre_xy)?('('+s.centre_xy[0].toFixed(2)+', '+s.centre_xy[1].toFixed(2)+')'):'--';
  document.getElementById('c10').textContent=(found&&s.centre_xy_r10)?('('+s.centre_xy_r10[0].toFixed(2)+', '+s.centre_xy_r10[1].toFixed(2)+')'):'--';
  if(found&&s.radius_shift_px!==null&&s.radius_shift_px!==undefined&&s.radius_shift_px>1.5){
    document.getElementById('c10').className='warn';
    document.getElementById('c10').textContent+='  ('+s.radius_shift_px.toFixed(1)+' px from r5)';
  } else { document.getElementById('c10').className=''; }
  if(s.paused){
    v.className='note warn';
    v.textContent='view paused: '+(s.pause_reason||'the camera is busy')+
      ' - the numbers above are the last frame, from '+String(s.utc||'--').slice(11,19)+' UTC';
    ex.className='big dim'; ey.className='big dim';
  }
  else if(!s.spot_found){
    v.className='note bad';
    const above=(s.peak_dn!==null&&s.peak_dn!==undefined&&s.black_dn)?(s.peak_dn-s.black_dn).toFixed(0):'?';
    v.textContent='no point source in the frame: brightest pixel is only '+above+
      ' DN above black. Check the lamp, the aperture and the polariser.';
  }
  else if(s.at_frame_edge){
    v.className='note bad';
    v.textContent='the spot is at the frame border; move it inward or the PSF is cut off';
  }
  else if(Math.abs(dx)<=tol&&Math.abs(dy)<=tol){
    v.className='note ok';
    v.textContent='inside the working box (+-'+tol+' px) - good enough, no pixel hunting';
  }
  else {
    v.className='note '+(Math.abs(dx)<=tol*2&&Math.abs(dy)<=tol*2?'warn':'bad');
    let txt=[];
    if(Math.abs(dx)>tol) txt.push(Math.abs(dx).toFixed(0)+' px '+(dx>0?'right of':'left of')+' the box centre');
    if(Math.abs(dy)>tol) txt.push(Math.abs(dy).toFixed(0)+' px '+(dy>0?'below':'above'));
    v.textContent=txt.join(', ');
  }
  // Fill reads how much of the box is still unused: full = spot inside.
  const bar=document.getElementById('fill');
  if(!found||s.at_frame_edge){bar.style.width='0%';}
  else{
    const used=Math.max(Math.abs(dx),Math.abs(dy))/tol;
    bar.style.width=Math.max(0,Math.min(100,(1-used/2)*100)).toFixed(0)+'%';
    bar.style.background=used<=1?'#3fb950':(used<=2?'#d29922':'#f85149');
  }

  // Whole frame, shrunk.  The spot is drawn wherever it actually is, so a spot
  // that walked out of the working area cannot hide behind a crop edge.
  const oc=document.getElementById('overview'), octx=oc.getContext('2d');
  paint(oc, s.patch_overview, s.overview_scale);
  const ovOrigin=s.overview_origin_xy||[0,0], of=s.overview_factor||1;
  const okx=oc.width/s.patch_overview[0].length, oky=oc.height/s.patch_overview.length;
  const toO=(x,y)=>[(x-ovOrigin[0])/of*okx, (y-ovOrigin[1])/of*oky];
  (s.trail||[]).forEach(q=>{const p=toO(q[0],q[1]);
    if(p[0]>0&&p[1]>0&&p[0]<oc.width&&p[1]<oc.height){
      octx.fillStyle='rgba(139,148,158,.6)'; octx.fillRect(p[0]-1,p[1]-1,2,2);}});
  if(s.target_xy){
    const a=toO(s.target_xy[0]-tol,s.target_xy[1]-tol);
    const b=toO(s.target_xy[0]+tol,s.target_xy[1]+tol);
    octx.strokeStyle='#f0883e'; octx.lineWidth=1; octx.setLineDash([5,3]);
    octx.strokeRect(a[0],a[1],b[0]-a[0],b[1]-a[1]); octx.setLineDash([]);
    const c=toO(s.target_xy[0],s.target_xy[1]);
    cross(octx,c[0],c[1],7,'#f0883e');
  }
  if(found&&s.centre_xy){const p=toO(s.centre_xy[0],s.centre_xy[1]);
    ring(octx,p[0],p[1],9,'#39d0d8',2); cross(octx,p[0],p[1],6,'#39d0d8');}
  const shape=s.frame_shape||[0,0];
  document.getElementById('ovcap').textContent='whole frame '+shape[0]+'x'+shape[1]+
    ', 1 screen px = '+of.toFixed(0)+' image px: orange = target, cyan = centre';

  const zc=document.getElementById('zoom'), zctx=zc.getContext('2d');
  paint(zc, s.patch_zoom, s.zoom_scale);
  const zOrigin=s.zoom_origin_xy||[0,0];
  const zk=zc.width/s.patch_zoom[0].length;
  const toZ=(x,y)=>[(x-zOrigin[0]+0.5)*zk, (y-zOrigin[1]+0.5)*zk];
  if(found&&s.centre_xy){const p=toZ(s.centre_xy[0],s.centre_xy[1]);
    cross(zctx,p[0],p[1],12,'#39d0d8');}
  if(s.target_xy){const p=toZ(s.target_xy[0],s.target_xy[1]);
    if(p[0]>2&&p[1]>2&&p[0]<zc.width-2&&p[1]<zc.height-2) ring(zctx,p[0],p[1],9,'#f0883e',1.5);}
  document.getElementById('zcap').textContent='zoom +-'+s.zoom_half_px+' px, full resolution ('+
    zk.toFixed(1)+' screen px per image px)'+(found?'':'  - fitted to the target, no spot');
  const rows=(s.history||[]).slice().reverse().map(h=>
    '<tr><td class="dim">'+(h.utc||'').slice(11,19)+'</td><td>'+f(h.dx,1)+'</td><td>'+f(h.dy,1)+
    '</td><td class="dim">'+f(h.roll,2)+'</td></tr>').join('');
  document.getElementById('hist').innerHTML=rows;
}
let busy=false, etag='';
async function tick(){
  if(busy)return; busy=true;
  try{
    const r=await fetch('state.json?etag='+encodeURIComponent(etag),{cache:'no-store'});
    // 304: the server has no newer frame, so nothing is transferred.
    if(r.status!==304){
      const tag=r.headers.get('ETag'); if(tag){etag=tag;}
      draw(await r.json());
    }
  }catch(e){}
  busy=false;
}
setInterval(tick,700); tick();
</script></body></html>
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def box_mean(image, radius=2):
    """Mean over a ``(2*radius+1)^2`` window, edges clipped.

    A single stuck pixel at the ceiling is not a point source.  Locating the
    spot on a small box mean first means one hot pixel cannot hijack the search,
    while the centre itself is still measured on the raw pixels.
    """
    image = np.asarray(image, dtype=float)
    height, width = image.shape
    integral = np.zeros((height + 1, width + 1), dtype=float)
    integral[1:, 1:] = image.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(height) - radius, 0, height)
    y1 = np.clip(np.arange(height) + radius + 1, 0, height)
    x0 = np.clip(np.arange(width) - radius, 0, width)
    x1 = np.clip(np.arange(width) + radius + 1, 0, width)
    total = (integral[y1][:, x1] - integral[y0][:, x1]
             - integral[y1][:, x0] + integral[y0][:, x0])
    count = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return total / count


def block_mean(image, factor):
    """Integer block average, for shrinking a wide crop before it is sent."""
    image = np.asarray(image, dtype=float)
    if factor <= 1:
        return image
    height = (image.shape[0] // factor) * factor
    width = (image.shape[1] // factor) * factor
    trimmed = image[:height, :width]
    return trimmed.reshape(height // factor, factor, width // factor,
                           factor).mean(axis=(1, 3))


def find_spot(mean, black=0.0, margin=FRAME_MARGIN, radius=2):
    """Coarse location of the brightest compact feature, in frame pixels."""
    smoothed = box_mean(mean, radius)
    trimmed = smoothed.copy()
    trimmed[:margin, :] = -np.inf
    trimmed[-margin:, :] = -np.inf
    trimmed[:, :margin] = -np.inf
    trimmed[:, -margin:] = -np.inf
    iy, ix = np.unravel_index(int(np.argmax(trimmed)), trimmed.shape)
    return {"peak_xy": [float(ix), float(iy)],
            "box_mean_dn": float(trimmed[iy, ix]),
            "peak_dn": float(np.asarray(mean)[iy, ix]),
            "above_black_dn": float(trimmed[iy, ix] - black)}


def detect(mean, black, refine_half=REFINE_HALF, radii=RADII_PX, edge_margin=FRAME_MARGIN):
    """Sub-pixel centre of the spot over the whole frame, in binned pixels.

    The disc centroid is computed twice.  ``radii[0]`` = 5 px is the convention
    the session analysis uses (it averages ``cos 4 theta`` away while the disc
    still sits inside the spot).  ``radii[1]`` = 10 px is a deliberate
    cross-check: if the two disagree, either the tail is asymmetric or the
    small disc is being pulled by the four-fold core, and the operator should
    not trust the sub-pixel part of the number.
    """
    found = find_spot(mean, black)
    ix, iy = int(found["peak_xy"][0]), int(found["peak_xy"][1])
    height, width = mean.shape
    y0 = max(0, iy - refine_half)
    x0 = max(0, ix - refine_half)
    patch = np.asarray(mean[y0:y0 + 2 * refine_half + 1,
                           x0:x0 + 2 * refine_half + 1], dtype=np.float64)
    fits = [ra.refine_center(patch, peak_xy=(ix - x0, iy - y0), black=black,
                             centroid_radius=float(radius)) for radius in radii]
    primary = fits[0]
    centre = (float(primary["center_xy"][0]) + x0,
              float(primary["center_xy"][1]) + y0)
    wide = (float(fits[-1]["center_xy"][0]) + x0,
            float(fits[-1]["center_xy"][1]) + y0)
    return {
        "peak_xy": found["peak_xy"],
        "peak_dn": found["peak_dn"],
        "box_mean_dn": found["box_mean_dn"],
        "centre_xy": [centre[0], centre[1]],
        "centre_xy_r10": [wide[0], wide[1]],
        "radius_shift_px": float(np.hypot(centre[0] - wide[0], centre[1] - wide[1])),
        "radii_px": [float(radius) for radius in radii],
        "centre_usable": bool(primary["usable"]),
        "centre_drift_px": float(primary["drift_from_anchor_px"]),
        "centre_anchor": primary["anchor_source"],
        "spot_found": bool(found["above_black_dn"] > 40.0),
        # A centre pinned to the frame border is not the spot, it is the
        # search window running out; say so instead of reporting 0 px.
        "at_frame_edge": bool(min(ix, iy, width - 1 - ix, height - 1 - iy)
                              <= edge_margin),
    }


def zoom_anchor(measured, target):
    """Where the zoom looks: the measured spot, or the target if it is missing.

    When the spot is gone the centroid is a fit to noise, so zooming there would
    show an empty patch at an arbitrary place.  The target is the useful guess.
    """
    if measured.get("spot_found"):
        return (float(measured["centre_xy"][0]), float(measured["centre_xy"][1]))
    return (float(target[0]), float(target[1]))


def crop(mean, centre, black, half):
    """Fixed-size crop around a float centre; returns the crop and its origin."""
    height, width = mean.shape
    x0 = int(round(centre[0])) - half
    y0 = int(round(centre[1])) - half
    x0 = max(0, min(x0, width - (2 * half + 1)))
    y0 = max(0, min(y0, height - (2 * half + 1)))
    patch = np.asarray(mean[y0:y0 + 2 * half + 1, x0:x0 + 2 * half + 1],
                       dtype=np.float64) - float(black)
    return patch, (x0, y0)


def make_state(index, measured, tilt, target, references, history, band, exposure_us,
               seconds=None):
    dx = measured["centre_xy"][0] - target[0]
    dy = measured["centre_xy"][1] - target[1]
    state = {
        "index": index,
        "utc": utc_now(),
        "ok": True,
        "error": None,
        "band_nm": band,
        "exposure_us": exposure_us,
        "seconds": None if seconds is None else round(float(seconds), 2),
        "target_xy": [float(target[0]), float(target[1])],
        "centre_xy": measured["centre_xy"],
        "centre_xy_r10": measured.get("centre_xy_r10"),
        "radius_shift_px": measured.get("radius_shift_px"),
        "peak_xy": measured["peak_xy"],
        "peak_dn": measured["peak_dn"],
        "box_mean_dn": measured["box_mean_dn"],
        "spot_found": measured["spot_found"],
        "at_frame_edge": measured["at_frame_edge"],
        "centre_usable": measured["centre_usable"],
        "centre_drift_px": measured["centre_drift_px"],
        "delta_xy": [float(dx), float(dy)],
        "roll_deg": tilt.get("roll_deg"),
        "tilt_deg": tilt.get("tilt_deg"),
        "roll_spread_deg": tilt.get("roll_spread_tail_deg"),
        "buffer_mixed": tilt.get("buffer_mixed"),
        "references": references,
        "history": history,
    }
    roll = tilt.get("roll_deg")
    for key, name in (("p1", "delta_p1_deg"), ("p2", "delta_p2_deg")):
        reference = (references or {}).get(key) or {}
        value = reference.get("roll_deg")
        state[name] = (float(ra.wrap_deg(roll - value))
                       if roll is not None and value is not None else None)
    return state


def pause_state(previous, reason, now=None):
    """The frame the view keeps showing while a capture owns the camera.

    adb and the camera have one owner.  A bracket takes minutes, so the view
    cannot keep capturing during it; it republishes the last frame with a
    ``paused`` flag instead of going blank, so the operator's page stays open
    and still answers the only question that matters between moves: what the
    last good frame looked like.
    """
    if previous is None:
        return {"ok": False, "error": None, "paused": True,
                "pause_reason": reason or "the camera is busy with a capture",
                "paused_utc": now or utc_now()}
    payload = dict(previous)
    payload["paused"] = True
    payload["pause_reason"] = reason or "the camera is busy with a capture"
    payload["paused_utc"] = now or utc_now()
    return payload


def write_json(path, payload, body=None):
    """Publish in one atomic replace, so a reader never sees half a file."""
    temporary = Path(path).with_suffix(".tmp")
    temporary.write_bytes(body if body is not None
                          else json.dumps(payload).encode("utf-8"))
    # A reader that opened the target a moment ago can still hold it; on
    # Windows that denies the delete the replace needs, so retry briefly
    # instead of killing the view.
    for _ in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            time.sleep(0.05)
    temporary.replace(path)


class LiveResources:
    """The two resources the page fetches, held in memory.

    Serving from memory is not only faster than reading the files back.  On
    Windows a plain ``open()`` denies delete sharing, so a request arriving
    while ``state.json`` was being replaced made the replace fail with
    ``WinError 5``.  The published bytes and the on-disk copy are now separate,
    so a reader cannot block the writer.
    """

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def put(self, name, body, etag=None):
        with self._lock:
            self._items[name] = (body, etag)

    def get(self, name):
        with self._lock:
            return self._items.get(name)


def serve(directory, port, live=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            name = ("index.html" if parsed.path in ("/", "")
                    else parsed.path.lstrip("/"))
            item = live.get(name) if live is not None else None
            if item is None:
                path = Path(directory) / name
                if not path.is_file() or path.is_dir():
                    self.send_error(404)
                    return
                item = (path.read_bytes(), None)
            body, etag = item
            # The page asks again every 0.7 s but a frame takes 4 s; without
            # this it would pull a few hundred kB for nothing.
            if etag is not None:
                since = parse_qs(parsed.query).get("etag", [None])[0]
                if since == etag:
                    self.send_response(304)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            # The state carries two images as integer grids, which compress by
            # roughly 5x; the page is often opened from a phone.
            encoding = None
            if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body = gzip.compress(body, 5)
                encoding = "gzip"
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/html; charset=utf-8" if name.endswith(".html")
                             else "application/json")
            self.send_header("Cache-Control", "no-store")
            if etag is not None:
                self.send_header("ETag", etag)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=server.serve_forever,
                     kwargs={"poll_interval": 0.1}, daemon=True).start()
    return server


def read_black(device, run_id, phone):
    """The declared black level, read once from the run's characteristics."""
    try:
        raw = device.shell(["run-as", phone.PACKAGE, "cat",
                            f"files/captures/{run_id}/characteristics.json"])
        values = json.loads(raw)["values"]
        pattern = values.get("android.sensor.blackLevelPattern") or [64, 64, 64, 64]
        return float(sum(pattern) / len(pattern))
    except Exception:
        return 64.0


def drop_view_run(device, phone, run_id, keep=False):
    """Delete the phone's copy of a live frame once the frame is in memory.

    One 3264x2448 RAW frame is about 32 MB, and the view pulls one every few
    seconds.  Keeping them filled a 105 GB phone in a day, after which every
    capture failed to write its frame and the app left runs stuck in
    ``running``.  These runs carry ``not_session_data: true``: the operator
    reads them on the page, not from storage, so the phone keeps nothing.
    """
    if keep:
        return "kept"
    try:
        device.shell(["run-as", phone.PACKAGE, "rm", "-rf", f"files/captures/{run_id}"])
    except Exception as exc:      # a failed cleanup must not lose the frame
        return f"keep-failed:{type(exc).__name__}"
    return "deleted"


def capture_frame(device, args, index):
    """One short run through the probe app; only the raw plane is pulled.

    ``capture_run`` is the same verified path a session uses (it checks the
    phone's own run.json, the requested exposure and the frame count).  What is
    skipped is everything that exists to preserve an acquisition: no export
    archive, no DNG re-read, no full-frame float64 means.  The plane arrives
    through ``adb exec-out`` and is decoded in memory; its SHA-256 is checked by
    the decoder, so a truncated pull cannot pass as an image.  The phone's copy
    is deleted as soon as the frame has been decoded - see ``drop_view_run``.
    """
    from tools.android_raw_probe import device as phone
    from tools.android_raw_probe import raw_tools

    note = json.dumps({"purpose": "live_alignment_control_frame",
                       "band_nm": int(args.band),
                       "exposure_us": int(args.exposure_us),
                       "target_xy": [float(args.target_x), float(args.target_y)],
                       "not_session_data": True})
    started = time.time()
    state = phone.capture_run(
        device, exposure_us=int(args.exposure_us), iso=100, count=int(args.frames),
        raw_format="RAW_SENSOR", pair="none", scene="point", condition_verified=True,
        note=note, camera_id=args.camera_id, shading="off", tonemap="high_quality",
        display="black", companion_size="preview", wait_seconds=60)
    captur_s = time.time() - started
    run_id = state["run_id"]
    frames = state.get("frames") or []
    if not frames:
        raise RuntimeError("the run reports no frames")

    started = time.time()
    total = None
    phone_status = "not-pulled"
    try:
        for frame in frames:
            record = frame["raw"]
            meta = json.loads(device.shell(["run-as", phone.PACKAGE, "cat",
                                            f"files/captures/{run_id}/"
                                            f"{record['metadata_file']}"]))
            plane = device.call(["exec-out", "run-as", phone.PACKAGE, "cat",
                                 f"files/captures/{run_id}/"
                                 f"{meta['planes'][0]['file']}"],
                                timeout=60).stdout
            values = raw_tools.decode_plane(plane, meta).astype(np.float64)
            total = values if total is None else total + values
    finally:
        phone_status = drop_view_run(device, phone, run_id, args.keep_phone_frames)
    return (total / float(len(frames)), run_id,
            {"capture_s": captur_s, "pull_s": time.time() - started,
             "phone": phone_status})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--adb", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True,
                    help="scratch directory for the view and its log")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--camera-id", default=None)
    ap.add_argument("--band", type=int, default=450)
    ap.add_argument("--exposure-us", type=int, default=2000)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--keep-phone-frames", action="store_true",
                    help="leave each pulled frame on the phone (debugging only; one "
                         "frame is ~32 MB and the view produces one every few seconds, "
                         "which is enough to fill a phone in a day)")
    ap.add_argument("--target-x", type=float, default=1610.0)
    ap.add_argument("--target-y", type=float, default=1214.0)
    ap.add_argument("--p1-roll", type=float, default=-0.920,
                    help="settled roll of the reference mount state")
    ap.add_argument("--p2-roll", type=float, default=17.247,
                    help="settled roll of the rotated mount state")
    ap.add_argument("--overview-width", type=int, default=340,
                    help="columns in the whole-frame overview (it is shrunk, never cropped)")
    ap.add_argument("--zoom-half", type=int, default=80,
                    help="half width of the full-resolution zoom, binned px")
    ap.add_argument("--tolerance-px", type=float, default=150.0,
                    help="half width of the working box around the target; the spot only "
                         "has to be inside it, not on the pixel")
    ap.add_argument("--edge-margin-px", type=float, default=120.0,
                    help="a centre closer than this to the frame border is rejected")
    ap.add_argument("--black", type=float, default=None,
                    help="black level in DN; read from the run if omitted")
    ap.add_argument("--port", type=int, default=8712)
    ap.add_argument("--count", type=int, default=0,
                    help="stop after N updates (0 = until STOP_ALIGN.txt)")
    ap.add_argument("--pause-s", type=float, default=0.0)
    ap.add_argument("--save-png", type=Path, default=None)
    ap.add_argument("--no-serve", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(args.repo.resolve()))
    from tools.android_raw_probe import device as phone

    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / "index.html").write_text(PAGE, encoding="utf-8")
    live = LiveResources()
    live.put("index.html", PAGE.encode("utf-8"))
    state_path = args.work / "state.json"
    log_path = args.work / "align_log.jsonl"
    stop_path = args.work / "STOP_ALIGN.txt"
    pause_path = args.work / "PAUSE_ALIGN.txt"
    target = (float(args.target_x), float(args.target_y))
    references = {
        "p1": {"label": "P1", "roll_deg": float(args.p1_roll)},
        "p2": {"label": "P2", "roll_deg": float(args.p2_roll)},
    }

    server = None
    if not args.no_serve:
        server = serve(args.work, args.port, live)
        print(json.dumps({"serving": f"http://0.0.0.0:{args.port}/",
                          "work": str(args.work)}), flush=True)

    device = None
    black = args.black
    history = []
    trail = []
    index = 0
    # An integer stamp keeps the ETag free of characters that a query string
    # would mangle ('+' in an ISO timestamp decodes to a space).
    stamp = int(time.time())
    # The accelerometer read needs adb but not the camera, so it overlaps the
    # capture instead of running after it.  One worker: one tilt read per frame.
    pool = ThreadPoolExecutor(max_workers=1)
    last_payload = None
    paused_key = None
    while not args.count or index < args.count:
        if stop_path.exists():
            print(json.dumps({"stopped": "STOP_ALIGN.txt present",
                              "updates": index}), flush=True)
            break
        if pause_path.exists():
            # The bracket driver owns adb and the camera while it runs, so the
            # view stops capturing but keeps serving its last frame.
            try:
                reason = pause_path.read_text(encoding="utf-8-sig").strip()
            except OSError:
                reason = ""
            key = f"{index}:{reason}"
            if key != paused_key:
                payload = pause_state(last_payload, reason)
                body = json.dumps(payload).encode("utf-8")
                live.put("state.json", body, etag=f"{stamp}-pause-{index}-{len(reason)}")
                write_json(state_path, None, body=body)
                paused_key = key
                print(json.dumps({"paused": payload["pause_reason"],
                                  "showing_index": (last_payload or {}).get("index")}),
                      flush=True)
            time.sleep(1.0)
            continue
        paused_key = None
        index += 1
        started = time.time()
        tilt_future = None
        try:
            if device is None:
                device = phone.Device(args.adb, args.serial)
            tilt_future = pool.submit(read_tilt.reading, label=f"align_{index:03d}",
                                      adb=str(args.adb), serial=args.serial)
            mean, run_id, timings = capture_frame(device, args, index)
            if black is None:
                black = read_black(device, run_id, phone)
            measured = detect(mean, black, edge_margin=args.edge_margin_px)
            try:
                tilt = tilt_future.result()
            except Exception:
                tilt = {}      # a missing tilt must not throw away the frame
            seconds = time.time() - started
        except Exception as exc:      # a failed frame must not end the view
            device = None
            state = {"index": index, "utc": utc_now(), "ok": False,
                     "error": f"{type(exc).__name__}: {exc}", "seconds": None}
            write_json(state_path, state)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(state) + "\n")
            print(json.dumps(state), flush=True)
            time.sleep(1.0)
            continue

        state = make_state(index, measured, tilt, target, references, history,
                           args.band, args.exposure_us, seconds=seconds)
        payload = dict(state)
        payload["black_dn"] = None if black is None else float(black)
        payload["frame_shape"] = [int(mean.shape[1]), int(mean.shape[0])]

        # The whole frame, shrunk to ``overview_width`` columns.  Nothing is
        # cropped, so the spot cannot leave the picture without the operator
        # seeing that it did.
        overview_factor = max(1, int(round(mean.shape[1] / float(args.overview_width))))
        overview = block_mean(mean, overview_factor) - float(black if black else 0.0)
        payload["patch_overview"] = np.rint(np.clip(overview, 0, None)).astype(int).tolist()
        payload["overview_factor"] = float(overview_factor)
        payload["overview_origin_xy"] = [0.0, 0.0]
        payload["overview_scale"] = max(float(np.percentile(overview, 99.9)), 8.0)

        # Full-resolution zoom: on the measured centre, or on the target when
        # the spot has gone missing.
        zoom_centre = zoom_anchor(measured, target)
        zoom, zoom_origin = crop(mean, zoom_centre, black, args.zoom_half)
        payload["patch_zoom"] = np.rint(np.clip(zoom, 0, None)).astype(int).tolist()
        payload["zoom_origin_xy"] = [float(zoom_origin[0]), float(zoom_origin[1])]
        payload["zoom_half_px"] = int(args.zoom_half)
        payload["zoom_scale"] = max(float(np.percentile(zoom, 99.9)), 8.0)

        if measured["spot_found"]:
            trail.append([float(measured["centre_xy"][0]), float(measured["centre_xy"][1])])
        payload["trail"] = trail[-TRAIL_LEN:]
        payload["timings"] = {key: (round(float(value), 2)
                                    if isinstance(value, (int, float)) else value)
                              for key, value in timings.items()}
        payload["tolerance_px"] = float(args.tolerance_px)
        payload["edge_margin_px"] = float(args.edge_margin_px)
        history.append({"utc": state["utc"],
                        "dx": state["delta_xy"][0] if measured["spot_found"] else None,
                        "dy": state["delta_xy"][1] if measured["spot_found"] else None,
                        "roll": state["roll_deg"]})
        payload["history"] = history[-10:]
        body = json.dumps(payload).encode("utf-8")
        live.put("state.json", body, etag=f"{stamp}-{index}")
        write_json(state_path, None, body=body)
        last_payload = payload
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({key: state[key] for key in
                                     ("utc", "index", "delta_xy", "roll_deg", "tilt_deg",
                                      "peak_dn", "spot_found", "centre_usable",
                                      "radius_shift_px", "seconds")}) + "\n")
        print(json.dumps({"index": index,
                          "dx": round(state["delta_xy"][0], 1)
                          if measured["spot_found"] else None,
                          "dy": round(state["delta_xy"][1], 1)
                          if measured["spot_found"] else None,
                          "found": measured["spot_found"],
                          "r10_shift": round(state["radius_shift_px"], 2),
                          "roll": state["roll_deg"], "peak_dn": round(state["peak_dn"], 1),
                          "seconds": round(seconds, 1),
                          "parts": payload["timings"]}), flush=True)
        if args.save_png:
            save_png(args.save_png, overview, zoom, state, payload["overview_scale"])
        if args.pause_s:
            time.sleep(args.pause_s)

    pool.shutdown(wait=False)

    if server is not None:
        server.shutdown()
    print(json.dumps({"finished": True, "updates": index, "black_dn": black}), flush=True)
    return 0


def save_png(path, overview, zoom, state, overview_scale):
    """Optional side copy of the two images, for a report figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(9.0, 4.4), dpi=110)
    axes[0].imshow(np.arcsinh(np.clip(overview, 0, None) / max(overview_scale / 40.0, 1.0)),
                   cmap="gray")
    axes[0].set_title("whole frame", fontsize=9)
    axes[1].imshow(np.arcsinh(np.clip(zoom, 0, None) / max(overview_scale / 60.0, 1.0)),
                   cmap="gray")
    axes[1].set_title("full-resolution zoom", fontsize=9)
    for axis in axes:
        axis.set_xticks([])
        axis.set_yticks([])
    dx, dy = state["delta_xy"]
    figure.suptitle(f"dX {dx:+.2f} px   dY {dy:+.2f} px   roll "
                    f"{state['roll_deg']:+.3f} deg"
                    f"{'' if state['spot_found'] else '   (no spot)'}", fontsize=10)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)


if __name__ == "__main__":
    raise SystemExit(main())
