package org.udc.rawprobe;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.*;
import android.util.Range;
import android.util.Size;
import android.view.Surface;
import android.view.WindowManager;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.*;

public final class MainActivity extends Activity {
    private HandlerThread thread;
    private Handler worker;
    private CameraManager manager;
    private TextView status;
    private Spinner cameras,format,pair,scene;
    private EditText exposure,iso,count,note;
    private CheckBox verified;
    private ScrollView controls;
    private TextView captureOverlay;
    private final ArrayList<String> frontIds=new ArrayList<>();
    private JSONObject inventory;
    private volatile Run active;
    private boolean autoPending;

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        thread=new HandlerThread("raw-probe");thread.start();worker=new Handler(thread.getLooper());
        manager=(CameraManager)getSystemService(CAMERA_SERVICE);
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(20,12,20,12);
        TextView title=new TextView(this);title.setText("UDC RAW Probe — no root\nSelect a FRONT camera. Confirm the actual illumination; metadata is not optical proof.");box.addView(title);
        cameras=spinner(box,"Front camera",new String[]{"Waiting for CAMERA permission"});
        format=spinner(box,"RAW format",new String[]{"RAW_SENSOR","RAW10"});
        pair=spinner(box,"Same-request companion",new String[]{"none","JPEG","YUV"});
        exposure=field(box,"Exposure (microseconds)","10000");iso=field(box,"ISO (sensitivity, not calibrated analog gain)","100");count=field(box,"Frames","10");
        scene=spinner(box,"Illumination label",new String[]{"unspecified","dark","flat","point"});
        note=field(box,"Source / wavelength / geometry / display state / notes","");
        verified=new CheckBox(this);verified.setText("I have physically verified this illumination condition");box.addView(verified);
        Button enumerate=new Button(this);enumerate.setText("Enumerate cameras");box.addView(enumerate);enumerate.setOnClickListener(v->enumerate());
        Button capture=new Button(this);capture.setText("Capture batch");box.addView(capture);capture.setOnClickListener(v->start(false));
        status=new TextView(this);status.setTextIsSelectable(true);box.addView(status);
        controls=new ScrollView(this);controls.addView(box);
        android.widget.FrameLayout root=new android.widget.FrameLayout(this);root.setBackgroundColor(android.graphics.Color.BLACK);root.addView(controls);
        captureOverlay=new TextView(this);captureOverlay.setBackgroundColor(android.graphics.Color.BLACK);captureOverlay.setTextColor(android.graphics.Color.DKGRAY);captureOverlay.setGravity(android.view.Gravity.BOTTOM);captureOverlay.setText("RAW capture active — keep illumination fixed");captureOverlay.setVisibility(android.view.View.GONE);root.addView(captureOverlay,new android.widget.FrameLayout.LayoutParams(-1,-1));setContentView(root);
        autoPending=getIntent().getBooleanExtra("auto",false);
        if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED) requestPermissions(new String[]{Manifest.permission.CAMERA},1);
        else enumerate();
    }
    private Spinner spinner(LinearLayout b,String label,String[] values){TextView l=new TextView(this);l.setText(label);b.addView(l);Spinner s=new Spinner(this);s.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,values));b.addView(s);return s;}
    private EditText field(LinearLayout b,String label,String value){TextView l=new TextView(this);l.setText(label);b.addView(l);EditText e=new EditText(this);e.setSingleLine(true);e.setText(value);b.addView(e);return e;}
    private void say(String text){android.util.Log.i("UdcRawProbe",text);runOnUiThread(()->status.append(text+"\n"));}
    @Override public void onRequestPermissionsResult(int r,String[] p,int[] g){super.onRequestPermissionsResult(r,p,g);if(g.length>0&&g[0]==PackageManager.PERMISSION_GRANTED)enumerate();else say("CAMERA permission denied; no capture");}
    @Override public void onNewIntent(Intent i){super.onNewIntent(i);setIntent(i);autoPending=i.getBooleanExtra("auto",false);if(active==null)enumerate();else say("Rejected new request: capture already running");}
    @Override public void onPause(){super.onPause();worker.post(()->{if(active!=null)active.fail("activity_paused","Camera foreground lost; batch aborted",null);});}
    @Override public void onDestroy(){worker.post(()->{if(active!=null)active.fail("activity_destroyed","Activity closed",null);thread.quitSafely();});super.onDestroy();}
    private void enumerate(){
        if(active!=null){say("Cannot enumerate during capture");return;}
        try{
            JSONArray all=new JSONArray();frontIds.clear();ArrayList<String> labels=new ArrayList<>();
            for(String id:manager.getCameraIdList()){
                CameraCharacteristics c=manager.getCameraCharacteristics(id);Integer facing=c.get(CameraCharacteristics.LENS_FACING);
                JSONObject entry=new JSONObject().put("camera_id",id).put("lens_facing",MetadataJson.encode(facing)).put("characteristics",MetadataJson.characteristics(c));all.put(entry);
                StreamConfigurationMap m=c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
                say("Camera "+id+" facing="+facing+" formats="+(m==null?"null":Arrays.toString(m.getOutputFormats())));
                if(facing!=null&&facing==CameraCharacteristics.LENS_FACING_FRONT){frontIds.add(id);labels.add(id+" (FRONT)");}
            }
            inventory=new JSONObject().put("schema_version",1).put("device_model",Build.MODEL).put("firmware_fingerprint",Build.FINGERPRINT).put("android_sdk",Build.VERSION.SDK_INT).put("cameras",all);
            File folder=new File(getFilesDir(),"inventories");folder.mkdirs();writeJson(new File(folder,"inventory_"+System.currentTimeMillis()+"_"+UUID.randomUUID().toString().substring(0,8)+".json"),inventory);
            cameras.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,labels.isEmpty()?new String[]{"No public front camera"}:labels.toArray(new String[0])));
            say("Inventory saved; public FRONT cameras="+frontIds.size());
            if(autoPending){autoPending=false;start(true);}
        }catch(Exception e){say("Enumeration failed: "+e);}
    }
    private void start(boolean fromIntent){
        if(active!=null){say("Batch already active");return;}
        try{
            Intent i=getIntent();String id;
            if(fromIntent&&!i.hasExtra("camera_id")&&frontIds.size()!=1)throw new IllegalArgumentException("Automatic capture requires one public FRONT camera or an explicit enumerated camera_id");
            if(fromIntent&&i.hasExtra("camera_id"))id=i.getStringExtra("camera_id");
            else{int pos=cameras.getSelectedItemPosition();if(pos<0||pos>=frontIds.size())throw new IllegalArgumentException("No selectable front camera");id=frontIds.get(pos);}
            if(!frontIds.contains(id))throw new IllegalArgumentException("Requested ID is not an enumerated public FRONT camera");
            String fmt=fromIntent?i.getStringExtra("raw_format"):format.getSelectedItem().toString();if(fmt==null)fmt="RAW_SENSOR";
            int f=fmt.equals("RAW_SENSOR")?ImageFormat.RAW_SENSOR:fmt.equals("RAW10")?ImageFormat.RAW10:-1;if(f<0)throw new IllegalArgumentException("Unknown RAW format");
            String paired=fromIntent?i.getStringExtra("pair"):pair.getSelectedItem().toString();if(paired==null)paired="none";
            if(!Arrays.asList("none","JPEG","YUV").contains(paired))throw new IllegalArgumentException("Invalid companion");
            long us=fromIntent?i.getLongExtra("exposure_us",10000):Long.parseLong(exposure.getText().toString());
            int sensitivity=fromIntent?i.getIntExtra("iso",100):Integer.parseInt(iso.getText().toString());
            int n=fromIntent?i.getIntExtra("count",10):Integer.parseInt(count.getText().toString());
            String condition=fromIntent?i.getStringExtra("scene"):scene.getSelectedItem().toString();if(condition==null)condition="unspecified";
            if(!Arrays.asList("unspecified","dark","flat","point").contains(condition))throw new IllegalArgumentException("Invalid illumination label");
            String notes=fromIntent?i.getStringExtra("note"):note.getText().toString();
            boolean confirm=fromIntent?i.getBooleanExtra("condition_verified",false):verified.isChecked();
            if(n<1||n>100||us<=0||us>Long.MAX_VALUE/1000)throw new IllegalArgumentException("Invalid exposure/frame count (1..100)");
            Run r=new Run(id,f,paired,us*1000,sensitivity,n,condition,confirm,notes==null?"":notes);active=r;showCapture(true);worker.postDelayed(r::open,500);
        }catch(Exception e){say("Capture rejected: "+e);}
    }
    private void showCapture(boolean enabled){runOnUiThread(()->{controls.setVisibility(enabled?android.view.View.GONE:android.view.View.VISIBLE);captureOverlay.setVisibility(enabled?android.view.View.VISIBLE:android.view.View.GONE);});}
    static void writeJson(File file,JSONObject j)throws Exception{
        File tmp=new File(file.getParentFile(),file.getName()+".tmp");
        if(tmp.exists())throw new IOException("Pending metadata file already exists: "+tmp.getName());
        try(FileOutputStream out=new FileOutputStream(tmp)){out.write(j.toString(2).getBytes("UTF-8"));out.getFD().sync();}
        try { java.nio.file.Files.move(tmp.toPath(),file.toPath(),java.nio.file.StandardCopyOption.ATOMIC_MOVE,java.nio.file.StandardCopyOption.REPLACE_EXISTING); } catch(java.nio.file.AtomicMoveNotSupportedException e) { java.nio.file.Files.move(tmp.toPath(),file.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING); }
    }
    static String hex(byte[] a){StringBuilder b=new StringBuilder();for(byte v:a)b.append(String.format(Locale.US,"%02x",v&255));return b.toString();}
    private static boolean has(int[] a,int value){if(a!=null)for(int x:a)if(x==value)return true;return false;}
    private static Size choose(StreamConfigurationMap m,int f,boolean raw){
        ArrayList<Size> sizes=new ArrayList<>();Size[] a=m.getOutputSizes(f);if(a!=null)sizes.addAll(Arrays.asList(a));
        Size[] high=m.getHighResolutionOutputSizes(f);if(high!=null)sizes.addAll(Arrays.asList(high));
        if(sizes.isEmpty())throw new IllegalArgumentException("Format not advertised: "+f);
        for(Size s:sizes)if(s.getWidth()==(raw?3264:640)&&s.getHeight()==(raw?2448:480))return s;
        sizes.sort((x,y)->Long.compare((long)x.getWidth()*x.getHeight(),(long)y.getWidth()*y.getHeight()));return raw?sizes.get(sizes.size()-1):sizes.get(0);
    }
    private final class Run {
        final String id,companion,condition,notes;final int rawFormat,sensitivity,total;final long exposureNs;final boolean confirmed;
        final File dir;final JSONObject state=new JSONObject();final JSONArray frames=new JSONArray(),events=new JSONArray();
        CameraCharacteristics characteristics;CameraDevice device;CameraCaptureSession session;ImageReader rawReader,companionReader;
        TotalCaptureResult result;final Map<String,Image> images=new HashMap<>();final ArrayList<Image> waiting=new ArrayList<>();
        CaptureRequest request;int index;boolean done;boolean artifactError;Runnable deadline;
        Run(String id,int f,String pair,long ns,int iso,int n,String scene,boolean confirmed,String notes)throws Exception{
            this.id=id;rawFormat=f;companion=pair;exposureNs=ns;sensitivity=iso;total=n;condition=scene;this.confirmed=confirmed;this.notes=notes;
            File parent=new File(getFilesDir(),"captures");parent.mkdirs();dir=new File(parent,"run_"+System.currentTimeMillis()+"_"+UUID.randomUUID().toString().substring(0,8));if(!dir.mkdir())throw new IOException("Cannot create unique run directory");
            state.put("schema_version",1).put("run_id",dir.getName()).put("status","running").put("capture_complete",false).put("run_succeeded",false)
                .put("camera_id",id).put("lens_facing","FRONT").put("raw_format",f).put("companion",pair).put("requested_exposure_ns",ns).put("requested_iso",iso).put("requested_frames",n)
                .put("condition",scene).put("condition_verified_by_operator",confirmed).put("condition_notes",notes).put("created_unix_ms",System.currentTimeMillis())
                .put("firmware_fingerprint",Build.FINGERPRINT).put("device_model",Build.MODEL).put("source_commit",BuildInfo.COMMIT).put("source_dirty",BuildInfo.DIRTY).put("app_source_sha256",BuildInfo.SOURCE_SHA256)
                .put("byte_order",ByteOrder.nativeOrder().toString()).put("frames",frames).put("events",events)
                .put("display_state","Static black app viewport with dim text at bottom; OS bars and vendor UDC display patch are not controlled or verified")
                .put("display_settle_delay_ms",500).put("window_screen_brightness",getWindow().getAttributes().screenBrightness)
                .put("screen_brightness_setting",android.provider.Settings.System.getInt(getContentResolver(),android.provider.Settings.System.SCREEN_BRIGHTNESS,-1))
                .put("screen_brightness_mode",android.provider.Settings.System.getInt(getContentResolver(),android.provider.Settings.System.SCREEN_BRIGHTNESS_MODE,-1));
            writeJson(new File(dir,"run.json"),state);writeJson(new File(dir,"inventory.json"),inventory);
        }
        void event(String type,String message)throws Exception{events.put(new JSONObject().put("unix_ms",System.currentTimeMillis()).put("type",type).put("message",message));writeJson(new File(dir,"run.json"),state);say(dir.getName()+": "+type+" "+message);}
        void open(){if(done)return;try{
            characteristics=manager.getCameraCharacteristics(id);
            if(!has(characteristics.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES),CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_RAW)||!has(characteristics.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES),CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR))throw new IllegalArgumentException("RAW and MANUAL_SENSOR capabilities required");
            Range<Long> e=characteristics.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE);Range<Integer> s=characteristics.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE);
            if(e==null||s==null||!e.contains(exposureNs)||!s.contains(sensitivity))throw new IllegalArgumentException("Requested exposure/ISO outside advertised bounds");
            writeJson(new File(dir,"characteristics.json"),MetadataJson.characteristics(characteristics));
            StreamConfigurationMap map=characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);if(map==null)throw new IllegalArgumentException("No stream configuration map");
            Size raw=choose(map,rawFormat,true);state.put("raw_size",MetadataJson.encode(raw));state.put("target_raw_size_available",raw.getWidth()==3264&&raw.getHeight()==2448);
            rawReader=ImageReader.newInstance(raw.getWidth(),raw.getHeight(),rawFormat,3);rawReader.setOnImageAvailableListener(this::arrived,worker);
            if(!companion.equals("none")){int fmt=companion.equals("JPEG")?ImageFormat.JPEG:ImageFormat.YUV_420_888;Size size=choose(map,fmt,false);state.put("companion_size",MetadataJson.encode(size));companionReader=ImageReader.newInstance(size.getWidth(),size.getHeight(),fmt,3);companionReader.setOnImageAvailableListener(this::arrived,worker);}
            event("opening","FRONT id="+id+" RAW="+raw+" format="+rawFormat+" companion="+companion);
            armTimeout("camera_open_timeout");
            manager.openCamera(id,new CameraDevice.StateCallback(){
                @Override public void onOpened(CameraDevice c){if(done){c.close();return;}device=c;configure();}
                @Override public void onDisconnected(CameraDevice c){c.close();fail("camera_disconnected","Camera disconnected",null);}
                @Override public void onError(CameraDevice c,int error){c.close();fail("camera_error","CameraDevice error="+error,null);}
            },worker);
        }catch(Exception e){fail("open_failed",e.toString(),e);}}
        void configure(){try{
            ArrayList<Surface> surfaces=new ArrayList<>();surfaces.add(rawReader.getSurface());if(companionReader!=null)surfaces.add(companionReader.getSurface());
            state.put("surface_count",surfaces.size());event("configuring","One session, one request targets all selected surfaces");armTimeout("session_configuration_timeout");
            device.createCaptureSession(surfaces,new CameraCaptureSession.StateCallback(){
                @Override public void onConfigured(CameraCaptureSession s){if(done){s.close();return;}session=s;next();}
                @Override public void onConfigureFailed(CameraCaptureSession s){s.close();fail("stream_combination_failed","Selected RAW/companion session rejected; no separate-exposure fallback",null);}
            },worker);
        }catch(Exception e){fail("session_creation_failed",e.toString(),e);}}
        <T> void optional(CaptureRequest.Builder b,CaptureRequest.Key<T> key,T value)throws Exception{
            List<CaptureRequest.Key<?>> keys=characteristics.getAvailableCaptureRequestKeys();
            if(keys!=null&&keys.contains(key))b.set(key,value);else event("request_key_unavailable",key.getName());
        }
        void next(){try{
            if(done)return;result=null;images.clear();
            CaptureRequest.Builder b=device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);b.addTarget(rawReader.getSurface());if(companionReader!=null)b.addTarget(companionReader.getSurface());
            b.set(CaptureRequest.CONTROL_MODE,CaptureRequest.CONTROL_MODE_OFF);b.set(CaptureRequest.CONTROL_AE_MODE,CaptureRequest.CONTROL_AE_MODE_OFF);b.set(CaptureRequest.CONTROL_AWB_MODE,CaptureRequest.CONTROL_AWB_MODE_OFF);
            b.set(CaptureRequest.SENSOR_EXPOSURE_TIME,exposureNs);b.set(CaptureRequest.SENSOR_SENSITIVITY,sensitivity);
            StreamConfigurationMap timings=characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            long minDuration=timings.getOutputMinFrameDuration(rawFormat,new Size(rawReader.getWidth(),rawReader.getHeight()));
            if(companionReader!=null)minDuration=Math.max(minDuration,timings.getOutputMinFrameDuration(companionReader.getImageFormat(),new Size(companionReader.getWidth(),companionReader.getHeight())));
            long duration=Math.max(minDuration,exposureNs+1000000L);Long maximum=characteristics.get(CameraCharacteristics.SENSOR_INFO_MAX_FRAME_DURATION);if(maximum!=null)duration=Math.min(duration,maximum);
            b.set(CaptureRequest.SENSOR_FRAME_DURATION,duration);
            if(has(characteristics.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES),CaptureRequest.CONTROL_AF_MODE_OFF))b.set(CaptureRequest.CONTROL_AF_MODE,CaptureRequest.CONTROL_AF_MODE_OFF);
            if(has(characteristics.get(CameraCharacteristics.NOISE_REDUCTION_AVAILABLE_NOISE_REDUCTION_MODES),CaptureRequest.NOISE_REDUCTION_MODE_OFF))optional(b,CaptureRequest.NOISE_REDUCTION_MODE,CaptureRequest.NOISE_REDUCTION_MODE_OFF);
            if(has(characteristics.get(CameraCharacteristics.EDGE_AVAILABLE_EDGE_MODES),CaptureRequest.EDGE_MODE_OFF))optional(b,CaptureRequest.EDGE_MODE,CaptureRequest.EDGE_MODE_OFF);
            if(has(characteristics.get(CameraCharacteristics.HOT_PIXEL_AVAILABLE_HOT_PIXEL_MODES),CaptureRequest.HOT_PIXEL_MODE_OFF))optional(b,CaptureRequest.HOT_PIXEL_MODE,CaptureRequest.HOT_PIXEL_MODE_OFF);
            if(has(characteristics.get(CameraCharacteristics.SHADING_AVAILABLE_MODES),CaptureRequest.SHADING_MODE_OFF))optional(b,CaptureRequest.SHADING_MODE,CaptureRequest.SHADING_MODE_OFF);
            if(has(characteristics.get(CameraCharacteristics.STATISTICS_INFO_AVAILABLE_LENS_SHADING_MAP_MODES),CaptureRequest.STATISTICS_LENS_SHADING_MAP_MODE_ON))optional(b,CaptureRequest.STATISTICS_LENS_SHADING_MAP_MODE,CaptureRequest.STATISTICS_LENS_SHADING_MAP_MODE_ON);
            request=b.build();writeJson(new File(dir,String.format(Locale.US,"frame_%04d_request.json",index)),MetadataJson.request(request));
            armTimeout("frame_timeout");session.capture(request,new CameraCaptureSession.CaptureCallback(){
                @Override public void onCaptureCompleted(CameraCaptureSession s,CaptureRequest q,TotalCaptureResult r){if(done)return;try{
                    result=r;writeJson(new File(dir,String.format(Locale.US,"frame_%04d_result.json",index)),MetadataJson.result(r));
                    if(r.get(CaptureResult.SENSOR_TIMESTAMP)==null){fail("missing_sensor_timestamp","Cannot prove image/result pairing",null);return;}
                    ArrayList<Image> pending=new ArrayList<>(waiting);waiting.clear();for(Image image:pending){if(done)image.close();else accept(image);}tryComplete();
                }catch(Exception e){fail("result_processing_failed",e.toString(),e);}}
                @Override public void onCaptureFailed(CameraCaptureSession s,CaptureRequest q,CaptureFailure f){fail("capture_failed","reason="+f.getReason()+" frame="+f.getFrameNumber()+" image_captured="+f.wasImageCaptured(),null);}
                @Override public void onCaptureBufferLost(CameraCaptureSession s,CaptureRequest q,Surface target,long frame){fail("buffer_lost","frame="+frame,null);}
            },worker);
        }catch(Exception e){fail("request_failed",e.toString(),e);}}
        void arrived(ImageReader reader){
            try{Image image;while((image=reader.acquireNextImage())!=null){if(done){image.close();continue;}if(result==null)waiting.add(image);else{accept(image);tryComplete();}}}
            catch(Exception e){fail("image_processing_failed",e.toString(),e);}
        }
        void accept(Image image)throws Exception{
            long sensor=result.get(CaptureResult.SENSOR_TIMESTAMP);
            if(image.getTimestamp()!=sensor){long timestamp=image.getTimestamp();image.close();fail("timestamp_mismatch","image="+timestamp+" sensor="+sensor+"; image not paired",null);return;}
            String role=image.getFormat()==rawFormat?"raw":"companion";
            if(images.containsKey(role)){image.close();fail("duplicate_image","Duplicate "+role+" timestamp",null);return;}
            images.put(role,image);
        }
        JSONObject saveImage(Image image,String role)throws Exception{
            String base=String.format(Locale.US,"frame_%04d_%s",index,role);
            JSONObject j=new JSONObject().put("role",role).put("camera_id",id).put("width",image.getWidth()).put("height",image.getHeight()).put("format",image.getFormat()).put("timestamp_ns",image.getTimestamp()).put("crop_rect",MetadataJson.encode(image.getCropRect())).put("byte_order",ByteOrder.nativeOrder().toString());JSONArray planes=new JSONArray();
            Image.Plane[] pp=image.getPlanes();
            for(int n=0;n<pp.length;n++){
                Image.Plane plane=pp[n];ByteBuffer b=plane.getBuffer().duplicate();int position=b.position(),limit=b.limit(),capacity=b.capacity();b.position(0);
                String name=base+"_plane"+n+".bin";File f=new File(dir,name);if(f.exists())throw new IOException("Refusing frame overwrite");MessageDigest hash=MessageDigest.getInstance("SHA-256");long bytes=0;
                try(FileOutputStream out=new FileOutputStream(f)){byte[] chunk=new byte[65536];while(b.hasRemaining()){int length=Math.min(chunk.length,b.remaining());b.get(chunk,0,length);out.write(chunk,0,length);hash.update(chunk,0,length);bytes+=length;}out.getFD().sync();}
                planes.put(new JSONObject().put("file",name).put("sha256",hex(hash.digest())).put("saved_bytes",bytes).put("saved_buffer_start",0).put("data_offset_bytes",position).put("buffer_original_position",position).put("buffer_limit",limit).put("buffer_capacity",capacity).put("row_stride",plane.getRowStride()).put("pixel_stride",plane.getPixelStride()));
            }
            j.put("planes",planes);writeJson(new File(dir,base+"_image.json"),j);return j.put("metadata_file",base+"_image.json");
        }
        void tryComplete()throws Exception{
            if(done||result==null||!images.containsKey("raw")||(companionReader!=null&&!images.containsKey("companion")))return;
            worker.removeCallbacks(deadline);JSONObject frame=new JSONObject().put("index",index).put("sensor_timestamp_ns",result.get(CaptureResult.SENSOR_TIMESTAMP)).put("frame_number",result.getFrameNumber()).put("same_capture_request",true).put("timestamp_pairing_verified",true);
            frame.put("raw",saveImage(images.get("raw"),"raw"));if(companionReader!=null)frame.put("companion",saveImage(images.get("companion"),"companion"));
            Long readExposure=result.get(CaptureResult.SENSOR_EXPOSURE_TIME);Integer readIso=result.get(CaptureResult.SENSOR_SENSITIVITY);Integer ae=result.get(CaptureResult.CONTROL_AE_MODE);
            frame.put("actual_exposure_ns",MetadataJson.encode(readExposure)).put("actual_iso",MetadataJson.encode(readIso)).put("actual_ae_mode",MetadataJson.encode(ae));
            boolean close=readExposure!=null&&readIso!=null&&ae!=null&&ae==CaptureRequest.CONTROL_AE_MODE_OFF&&Math.abs((double)readExposure-exposureNs)/exposureNs<=0.02&&Math.abs(readIso-sensitivity)<=Math.max(1,sensitivity*0.01);
            frame.put("manual_readback_within_tolerance",close).put("manual_readback_tolerance","exposure relative <= 2%; ISO <= max(1,1%); AE OFF; not an analog/digital gain calibration");
            if(rawFormat==ImageFormat.RAW_SENSOR){
                File dng=new File(dir,String.format(Locale.US,"frame_%04d.dng",index));
                try(DngCreator creator=new DngCreator(characteristics,result);FileOutputStream out=new FileOutputStream(dng)){creator.writeImage(out,images.get("raw"));out.getFD().sync();frame.put("dng_status","saved").put("dng_file",dng.getName());}
                catch(Exception e){artifactError=true;frame.put("dng_status","failed").put("dng_error",e.toString());}
            }else frame.put("dng_status","not_applicable_RAW10_DngCreator_writeImage_requires_RAW_SENSOR");
            for(Image im:images.values())im.close();images.clear();
            frame.put("complete",true);frames.put(frame);index++;writeJson(new File(dir,"run.json"),state);say("Saved "+index+"/"+total+" matched RAW frames; manual readback="+close);
            if(index>=total)finish();else worker.post(this::next);
        }
        void armTimeout(String message){if(deadline!=null)worker.removeCallbacks(deadline);deadline=()->fail(message,"No complete callback set within 30 seconds",null);worker.postDelayed(deadline,30000);}
        void finish(){if(done)return;done=true;cleanup();try{state.put("status",artifactError?"completed_with_artifact_errors":"completed").put("capture_complete",index==total).put("run_succeeded",index==total&&!artifactError).put("completed_frames",index).put("finished_unix_ms",System.currentTimeMillis());writeJson(new File(dir,"run.json"),state);say("Batch finished: "+dir.getName()+" capture_complete="+(index==total)+" run_succeeded="+(!artifactError));}catch(Exception e){say("FINAL MANIFEST WRITE FAILED: "+e);}active=null;showCapture(false);}
        void fail(String type,String message,Exception exception){if(done)return;done=true;cleanup();try{state.put("status","failed").put("capture_complete",false).put("run_succeeded",false).put("completed_frames",index).put("failure_type",type).put("failure_message",message).put("finished_unix_ms",System.currentTimeMillis());if(exception!=null){StringWriter w=new StringWriter();exception.printStackTrace(new PrintWriter(w));state.put("stacktrace",w.toString());}events.put(new JSONObject().put("unix_ms",System.currentTimeMillis()).put("type",type).put("message",message));writeJson(new File(dir,"run.json"),state);}catch(Exception e){say("FAILURE MANIFEST WRITE FAILED: "+e);}say("FAILED "+type+": "+message+"; partial files retained in "+dir.getName());active=null;showCapture(false);}
        void closeQuiet(AutoCloseable resource){if(resource!=null)try{resource.close();}catch(Exception e){say("Cleanup warning: "+e);}}
        void cleanup(){if(deadline!=null)worker.removeCallbacks(deadline);for(Image im:waiting)closeQuiet(im);waiting.clear();for(Image im:images.values())closeQuiet(im);images.clear();closeQuiet(session);closeQuiet(device);closeQuiet(rawReader);closeQuiet(companionReader);}
    }
}
