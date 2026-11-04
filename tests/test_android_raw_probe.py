"""Synthetic layout/traceability tests. These are not measured device frames."""
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest

spec = importlib.util.spec_from_file_location("raw_tools", Path(__file__).resolve().parents[1] / "tools/android_raw_probe/raw_tools.py")
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


def meta(data, fmt=32, width=4, height=2, row=12, pixel=2, offset=3, order="LITTLE_ENDIAN"):
    return {"role":"raw","camera_id":"front-test","format":fmt,"width":width,"height":height,"timestamp_ns":100,"byte_order":order,
        "planes":[{"file":"p.bin","saved_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"row_stride":row,"pixel_stride":pixel,"data_offset_bytes":offset}]}


def test_raw16_padding_offset_and_short_last_row():
    x=np.array([[0,1,1022,1023],[64,256,512,1000]],dtype=np.uint16)
    data=b"abc"+x[0].astype("<u2").tobytes()+b"PAD!"+x[1].astype("<u2").tobytes()
    y=r.decode_plane(data,meta(data));np.testing.assert_array_equal(y,x)
    assert y.dtype==np.uint16 and y.flags.owndata


def test_raw16_big_endian_and_pixel_stride():
    data=b"\x01\x23x\x03\xffx"*2
    np.testing.assert_array_equal(r.decode_plane(data,meta(data,width=2,row=6,pixel=3,offset=0,order="BIG_ENDIAN")),[[0x123,1023],[0x123,1023]])


def test_raw10_hand_encoded_low_bits_and_padding():
    data=b"xyz"+bytes([0,0,255,255,228])+b"PAD"+bytes([16,64,128,250,0])
    np.testing.assert_array_equal(r.decode_plane(data,meta(data,fmt=37,row=8,pixel=0)),[[0,1,1022,1023],[64,256,512,1000]])


def test_raw10_all_1024_values_multiple_groups():
    x=np.arange(1024,dtype=np.uint16).reshape(32,32);rows=[]
    for line in x:
        g=line.reshape(-1,4);p=np.empty((8,5),dtype=np.uint8);p[:,:4]=g>>2
        p[:,4]=(g[:,0]&3)|((g[:,1]&3)<<2)|((g[:,2]&3)<<4)|((g[:,3]&3)<<6)
        rows.append(p.tobytes()+b"PAD")
    data=b"".join(rows)
    np.testing.assert_array_equal(r.decode_plane(data,meta(data,fmt=37,width=32,height=32,row=43,pixel=0,offset=0)),x)


@pytest.mark.parametrize("change",[{"row_stride":4},{"data_offset_bytes":9},{"pixel_stride":0}])
def test_raw16_invalid_layout(change):
    data=bytes(16);m=meta(data,row=8,offset=0);m["planes"][0].update(change)
    with pytest.raises(ValueError):r.decode_plane(data,m)


def test_hash_and_truncation():
    data=bytes(16)
    with pytest.raises(ValueError,match="SHA256"):r.decode_plane(b"x"+data[1:],meta(data,row=8,offset=0))
    with pytest.raises(ValueError,match="Truncated"):r.decode_plane(data[:-1],meta(data[:-1],row=8,offset=0))


@pytest.mark.parametrize("change",[{"width":5},{"height":3},{"format":35}])
def test_raw10_invalid_shape_and_format(change):
    data=bytes(10);m=meta(data,fmt=37,row=5,pixel=0,offset=0);m.update(change)
    with pytest.raises(ValueError):r.decode_plane(data,m)


def test_path_escape(tmp_path):
    with pytest.raises(ValueError):r.local_file(tmp_path,"../outside.bin")


def write(path,obj):path.write_text(json.dumps(obj),encoding="utf-8")


def fake_run(tmp_path,name="synthetic",count=10,scene="point",verified=False,exposure=1000000,actual=None,dn=100):
    p=tmp_path/name;p.mkdir();frames=[]
    write(p/"characteristics.json",{"values":{"android.lens.facing":0,"android.sensor.info.colorFilterArrangement":3,"android.sensor.info.whiteLevel":1023,"android.sensor.blackLevelPattern":[64]*4,"android.sensor.info.lensShadingApplied":True},"errors":{}})
    write(p/"inventory.json",{"cameras":[{"camera_id":"front-test","lens_facing":0}]})
    for i in range(count):
        data=np.full((2,4),dn,dtype="<u2").tobytes();m=meta(data,row=8,offset=0);m["timestamp_ns"]=100+i;m["planes"][0]["file"]=f"f{i}.bin"
        (p/f"f{i}.bin").write_bytes(data);write(p/f"f{i}.json",m)
        write(p/f"frame_{i:04d}_result.json",{"values":{"android.sensor.timestamp":100+i,"android.sensor.exposureTime":exposure if actual is None else actual,"android.sensor.sensitivity":100,"android.control.aeMode":0}})
        write(p/f"frame_{i:04d}_request.json",{"values":{"android.sensor.exposureTime":exposure,"android.sensor.sensitivity":100}})
        frames.append({"index":i,"complete":True,"sensor_timestamp_ns":100+i,"manual_readback_within_tolerance":True,"raw":{"metadata_file":f"f{i}.json","timestamp_ns":100+i}})
    write(p/"run.json",{"schema_version":1,"run_id":name,"camera_id":"front-test","lens_facing":"FRONT","raw_format":32,"raw_size":{"width":4,"height":2},"firmware_fingerprint":"SYNTHETIC_NOT_DEVICE_DATA","requested_iso":100,"requested_exposure_ns":exposure,"requested_frames":count,"capture_complete":True,"run_succeeded":True,"condition":scene,"condition_verified_by_operator":verified,"condition_notes":"synthetic software fixture only","frames":frames})
    return p


def test_decode_end_to_end_preserves_dn_without_optical_claim(tmp_path):
    p=fake_run(tmp_path);s=r.decode_run(p,tmp_path/"decoded")
    assert s["validated_raw_frames"]==10 and s["all_manual_readbacks_within_tolerance"]
    assert s["declared_cfa"]=="BGGR" and not s["optical_diffraction_verified"]
    assert not s["illumination"]["operator_verified"] and not s["effective_sensor_bit_depth_verified"]
    np.testing.assert_array_equal(np.load(tmp_path/"decoded/frame_0000.npy"),np.full((2,4),100))
    assert (tmp_path/"decoded/first_frame_tiles.png").stat().st_size>0
    with pytest.raises(FileExistsError):r.decode_run(p,tmp_path/"decoded")


def test_manual_result_recomputed_instead_of_trusting_manifest(tmp_path):
    _,records=r.frame_records(fake_run(tmp_path,actual=5000000))
    assert all(not x["stats"]["manual_readback_within_tolerance"] for x in records)


def test_timestamp_mismatch(tmp_path):
    p=fake_run(tmp_path,count=1);j=r.load_json(p/"frame_0000_result.json");j["values"]["android.sensor.timestamp"]=999;write(p/"frame_0000_result.json",j)
    with pytest.raises(ValueError,match="timestamp"):r.frame_records(p)


def test_front_inventory_mismatch(tmp_path):
    p=fake_run(tmp_path,count=1);write(p/"inventory.json",{"cameras":[{"camera_id":"front-test","lens_facing":1}]})
    with pytest.raises(ValueError,match="inventory"):r.frame_records(p)
    write(p/"characteristics.json",{"values":{"android.lens.facing":1}})
    with pytest.raises(ValueError,match="front"):r.frame_records(p)


def test_unverified_conditions_not_linearity_evidence(tmp_path):
    paths=[fake_run(tmp_path,name=f"f{i}",count=3,scene="flat",exposure=(i+1)*1000000,dn=100+50*i) for i in range(3)]
    s=r.analyze_series(paths,tmp_path/"series")["exposure_linearity"]
    assert s["status"]=="insufficient_data" and not s["levels"]


@pytest.mark.parametrize("saturate",[False,True])
def test_verified_dark_subtracted_linearity_and_saturation_guard(tmp_path,saturate):
    paths=[]
    for i in range(1,4):
        paths += [fake_run(tmp_path,name=f"dark{i}",count=3,scene="dark",verified=True,exposure=i*1000000,dn=64),fake_run(tmp_path,name=f"flat{i}",count=3,scene="flat",verified=True,exposure=i*1000000,dn=1023 if saturate else 64+100*i)]
    result=r.analyze_series(paths,tmp_path/"series");s=result["exposure_linearity"]
    assert not result["diffraction_verified"]
    if saturate:assert s["status"]=="insufficient_data"
    else:assert s["status"]=="descriptive_fit_only" and s["slope_dn_per_ms"]==pytest.approx(100)


def test_opaque_metadata_is_explicitly_reported():
    value={"values":{"vendor.tag":{"serialization_status":"opaque_value_not_lossless","text":"opaque"}},"errors":{"bad.key":"read error"}}
    issues=r.serialization_issues(value)
    assert any("opaque" in x for x in issues) and any("bad.key" in x for x in issues)


def test_completed_run_requires_requested_frame_count(tmp_path):
    p=fake_run(tmp_path,count=1);j=r.load_json(p/"run.json");j["requested_frames"]=10;write(p/"run.json",j)
    with pytest.raises(ValueError,match="frame count"):r.frame_records(p)


def test_companion_wrong_timestamp_is_rejected(tmp_path):
    p=fake_run(tmp_path,count=1);j=r.load_json(p/"run.json");j["frames"][0]["companion"]={"timestamp_ns":999};write(p/"run.json",j)
    with pytest.raises(ValueError,match="Companion"):r.frame_records(p)


def test_companion_plane_hash_checked(tmp_path):
    p=fake_run(tmp_path,count=1);data=b"jpeg-test";(p/"jpeg.bin").write_bytes(data)
    write(p/"jpeg.json",{"timestamp_ns":100,"camera_id":"front-test","planes":[{"file":"jpeg.bin","saved_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}]})
    j=r.load_json(p/"run.json");j["frames"][0]["companion"]={"timestamp_ns":100,"metadata_file":"jpeg.json"};write(p/"run.json",j)
    assert len(r.frame_records(p)[1])==1
    (p/"jpeg.bin").write_bytes(b"corrupted")
    with pytest.raises(ValueError,match="Companion plane integrity"):r.frame_records(p)


def test_temporal_stability_uses_fixed_pixel_values(tmp_path):
    p=fake_run(tmp_path,count=3)
    for i,dn in enumerate([100,110,120]):
        data=np.full((2,4),dn,dtype="<u2").tobytes();(p/f"f{i}.bin").write_bytes(data)
        j=r.load_json(p/f"f{i}.json");j["planes"][0]["sha256"]=hashlib.sha256(data).hexdigest();write(p/f"f{i}.json",j)
    result=r.decode_run(p,tmp_path/"decoded")
    assert result["temporal_stability"]["median_fixed_pixel_std_dn"]==pytest.approx(10)
    np.testing.assert_array_equal(np.load(tmp_path/"decoded/temporal_mean_dn.npy"),np.full((2,4),110))
