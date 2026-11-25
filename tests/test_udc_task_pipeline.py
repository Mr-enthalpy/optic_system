"""Synthetic phone fixtures only: task routing, completion and shared analysis."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import numpy as np
import pytest

from test_android_raw_probe import fake_run, write
from tasks.udc.plan import UDCCapturePlan, UDCBatch
from tasks.udc.capture import run_udc_capture_plan
from tasks.udc import capture as pipeline
from tasks.udc.measurement import load_udc_measurement
from tasks.measurement.roi import ROI
from tasks.illumination import IlluminationSpec
from tools.android_raw_probe import device as phone


def plan(**updates):
    values=dict(plan_id="synthetic_test",condition="dark",source_description="synthetic fixture, not a measurement",
                display_state="synthetic fixed state",batches=(UDCBatch(1000,3),))
    values.update(updates)
    return UDCCapturePlan(**values)


def test_plan_has_no_lcd_pupil_and_preserves_reference_order():
    p=plan(batches=(UDCBatch(1000),UDCBatch(4000),UDCBatch(1000)))
    restored=UDCCapturePlan.from_dict(p.to_dict())
    assert [b.exposure_us for b in restored.batches]==[1000,4000,1000]
    with pytest.raises(ValueError,match="LCD"):
        UDCCapturePlan.from_dict({**p.to_dict(),"masks":[]})
    with pytest.raises(ValueError,match="point requires"):
        plan(condition="point")


@pytest.mark.parametrize("change",[{"frames":True},{"frames":101},{"exposure_us":0},{"exposure_us":1.5},
                                   {"shading":"auto"},{"tonemap":"linear"},{"display":"full_screen"}])
def test_invalid_batch_rejected(change):
    with pytest.raises(ValueError): UDCBatch(**{"exposure_us":1000,**change})


def test_full_companion_requires_a_pair_and_round_trips():
    with pytest.raises(ValueError,match="companion"):
        plan(companion_size="full")
    p=plan(pair="JPEG",companion_size="full")
    assert UDCCapturePlan.from_dict(p.to_dict()).companion_size=="full"


def test_batch_display_and_correction_controls_reach_each_capture(tmp_path,monkeypatch):
    install_synthetic_backend(tmp_path,monkeypatch)
    seen=[];installed=phone.capture_run
    def record(device,**kwargs):seen.append(kwargs);return installed(device,**kwargs)
    monkeypatch.setattr(phone,"capture_run",record)
    batches=(UDCBatch(1000,3,shading="off",display="black"),UDCBatch(1000,3,shading="on",display="white"))
    p=plan(batches=batches)
    assert UDCCapturePlan.from_dict(p.to_dict()).batches==batches
    out=tmp_path/"pipeline"
    result=run_udc_capture_plan(p,adb=Path("unused"),output=out,condition_verified=True)
    assert [(k["shading"],k["tonemap"],k["display"],k["companion_size"]) for k in seen]==[
        ("off","high_quality","black","preview"),("on","high_quality","white","preview")]
    assert [(b["shading"],b["display"]) for b in result["batches"]]==[("off","black"),("on","white")]
    assert json.loads((out/"plan.json").read_text())["batches"][1]["display"]=="white"


def test_no_stale_operator_confirmation_or_output_overwrite(tmp_path,monkeypatch):
    monkeypatch.setattr(phone,"Device",lambda *a:pytest.fail("must reject before device access"))
    with pytest.raises(ValueError,match="CURRENT"):
        run_udc_capture_plan(plan(),adb=Path("unused"),output=tmp_path/"new")
    assert not (tmp_path/"new").exists()
    with pytest.raises(FileExistsError):
        run_udc_capture_plan(plan(),adb=Path("unused"),output=tmp_path,condition_verified=True)


def test_external_point_illumination_is_not_a_tls_or_lcd_command():
    p=plan(condition="point",illumination=IlluminationSpec("monochromatic",550,None),source_distance_mm=500,aperture_diameter_um=100)
    assert UDCCapturePlan.from_dict(p.to_dict()).illumination.effective_wavelength_nm==550
    with pytest.raises(ValueError,match="TLS"):
        plan(condition="point",illumination=IlluminationSpec("monochromatic",550,550),source_distance_mm=500,aperture_diameter_um=100)
    with pytest.raises(ValueError,match="finite"):
        plan(condition="point",illumination=IlluminationSpec("monochromatic",float('nan'),None),source_distance_mm=500,aperture_diameter_um=100)


def install_synthetic_backend(tmp_path,monkeypatch,*,export_failure=False,failed_capture=False):
    source_root=tmp_path/"synthetic_phone";source_root.mkdir()
    state={};counter=[0]
    monkeypatch.setattr(phone,"Device",lambda *a:object())
    def capture(device,**kwargs):
        counter[0]+=1
        rid=f"run_1_{counter[0]:08d}"
        source=fake_run(source_root,name=rid,count=kwargs["count"],scene=kwargs["scene"],verified=kwargs["condition_verified"],exposure=kwargs["exposure_us"]*1000,
                        shading=kwargs.get("shading","off"),tonemap=kwargs.get("tonemap","high_quality"),
                        display=kwargs.get("display","app_viewport"),companion_size=kwargs.get("companion_size","preview"))
        value=json.loads((source/"run.json").read_text())
        value.update(status="completed",completed_frames=kwargs["count"])
        for i,f in enumerate(value["frames"]):
            f.update(dng_status="saved",dng_file=f"frame_{i:04d}.dng")
            (source/f["dng_file"]).write_bytes(b"II*\x00SYNTHETIC_HEADER_NOT_A_REAL_DNG")
        if failed_capture:value.update(run_succeeded=False,status="completed_with_artifact_errors")
        write(source/"run.json",value);state.update(source=source,run=value)
        if failed_capture:raise phone.CaptureFailure(value)
        return value
    def export(device,output,*,run_ids):
        assert run_ids==[state["run"]["run_id"]]
        output.mkdir(parents=True)
        (output/"original_export.tar").write_bytes(b"SYNTHETIC archive stand-in")
        if export_failure:raise TimeoutError("synthetic export timeout")
        dest=output/"device_files/captures"/run_ids[0]
        shutil.copytree(state["source"],dest)
        return {"export":str(output)}
    monkeypatch.setattr(phone,"capture_run",capture)
    monkeypatch.setattr(phone,"export",export)


def test_complete_udc_plan_reuses_native_decode_and_monitor(tmp_path,monkeypatch):
    install_synthetic_backend(tmp_path,monkeypatch)
    out=tmp_path/"pipeline"
    result=run_udc_capture_plan(plan(),adb=Path("unused"),output=out,condition_verified=True)
    assert result["capture_complete"] and result["run_succeeded"]
    assert not result["optical_diffraction_verified"]
    np.testing.assert_array_equal(np.load(out/"batch_000/decoded/frame_0000.npy"),np.full((2,4),100))
    status=json.loads((out/"status/state.json").read_text())
    assert status["completed"] and status["phase"]=="completed"


@pytest.mark.parametrize("mode",["export","capture"])
def test_failure_preserves_capture_fact_and_partial_files(tmp_path,monkeypatch,mode):
    install_synthetic_backend(tmp_path,monkeypatch,export_failure=mode=="export",failed_capture=mode=="capture")
    out=tmp_path/"pipeline"
    with pytest.raises((TimeoutError,phone.CaptureFailure)):
        run_udc_capture_plan(plan(),adb=Path("unused"),output=out,condition_verified=True)
    result=json.loads((out/"status/pipeline.json").read_text())
    assert result["capture_complete"] and not result["run_succeeded"]
    assert not result["batches"][0]["decode_complete"]
    assert (out/"batch_000/export/original_export.tar").exists()
    assert not json.loads((out/"status/state.json").read_text())["completed"]


def test_udc_roi_keeps_acquired_coordinates_parity_and_original_dn(tmp_path):
    source=fake_run(tmp_path,count=3,dn=100)
    full=load_udc_measurement(source)
    cropped=load_udc_measurement(source,roi=ROI(1,0,3,2))
    np.testing.assert_array_equal(cropped.mean_dn,full.mean_dn[:,1:])
    assert cropped.metadata["coordinate_frame"]=="acquired_frame"
    assert cropped.metadata["roi_parity_origin_yx"]==[0,1]
    assert not cropped.metadata["sensor_origin_verified"]
    assert cropped.energy_center()["fallback_used"]
    assert cropped.energy_center()["center_xy"]==(1.,0.)
    run=json.loads((source/"run.json").read_text());run["run_succeeded"]=False;write(source/"run.json",run)
    with pytest.raises(ValueError,match="successful"):load_udc_measurement(source)


def test_phone_task_imports_without_hdf5_or_lcd():
    code="import sys; sys.modules['h5py']=None; import tasks.udc.capture, tasks.udc.measurement; assert 'devices' not in sys.modules; assert 'tasks.psf' not in sys.modules"
    result=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
    assert result.returncode==0,result.stderr


def test_validate_only_never_opens_device(tmp_path,monkeypatch,capsys):
    from scripts.capture_udc import main
    source=tmp_path/"plan.json";write(source,plan().to_dict())
    monkeypatch.setattr(phone,"Device",lambda *a:pytest.fail("device opened"))
    assert main(["--plan",str(source),"--validate-only"])==0
    assert json.loads(capsys.readouterr().out)["condition"]=="dark"


def test_capture_rejects_false_success_and_preserves_terminal_state():
    class Device:
        started=False
        def runs(self):return {"run_1_00000001"} if self.started else set()
        def shell(self,args,**kwargs):
            if args[0]=="am":self.started=True;return ""
            return json.dumps({"run_id":"run_1_00000001","status":"completed","run_succeeded":True,"capture_complete":True,"completed_frames":0,"frames":[]})
    with pytest.raises(phone.CaptureFailure) as caught:phone.capture_run(Device(),exposure_us=1000,iso=100,count=3)
    assert caught.value.state["completed_frames"]==0


@pytest.mark.parametrize("wrong",[None,"requested_iso","requested_exposure_ns","condition"])
def test_native_capture_return_must_match_the_requested_plan(wrong):
    state=dict(run_id="run_1_00000001",status="completed",run_succeeded=True,capture_complete=True,
               completed_frames=3,frames=[{}, {}, {}],requested_frames=3,requested_iso=100,
               requested_exposure_ns=1000000,raw_format=32,condition="dark",condition_verified_by_operator=True,
               requested_shading_mode="off",requested_tonemap_mode="high_quality",
               requested_display_panel="app_viewport",requested_companion_size="preview")
    if wrong is not None:state[wrong]="wrong"
    class Device:
        started=False
        def runs(self):return {state["run_id"]} if self.started else set()
        def shell(self,args,**kwargs):
            if args[0]=="am":self.started=True;return ""
            return json.dumps(state)
    if wrong is None:
        assert phone.capture_run(Device(),exposure_us=1000,iso=100,count=3,scene="dark",condition_verified=True)==state
    else:
        with pytest.raises(phone.CaptureFailure):phone.capture_run(Device(),exposure_us=1000,iso=100,count=3,scene="dark",condition_verified=True)


def test_selected_export_is_lossless_and_does_not_include_other_runs(tmp_path,monkeypatch):
    rid="run_1_00000001";buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode="w") as tar:
        content=b"SYNTHETIC raw bytes";entry=tarfile.TarInfo("captures/"+rid+"/raw.bin");entry.size=len(content);tar.addfile(entry,io.BytesIO(content))
    class Device:
        prefix=["synthetic_adb"]
        def runs(self):return {rid,"run_2_00000002"}
        def shell(self,*args,**kwargs):return ""
    def run(command,**kwargs):
        assert command[-1]=="captures/"+rid and "inventories" not in command
        kwargs["stdout"].write(buffer.getvalue())
        return subprocess.CompletedProcess(command,0,stderr=b"")
    monkeypatch.setattr(phone.subprocess,"run",run)
    phone.export(Device(),tmp_path/"export",run_ids=[rid])
    assert (tmp_path/"export/device_files/captures"/rid/"raw.bin").read_bytes()==b"SYNTHETIC raw bytes"
    with pytest.raises(ValueError):phone.export(Device(),tmp_path/"bad",run_ids=["../escape"])
    assert not (tmp_path/"bad").exists()


def test_export_error_retains_archive_and_redacts_transport_id(tmp_path,monkeypatch):
    class Device:
        prefix=["synthetic_adb"]
        selected_serial="PRIVATE_TEST_ID"
    def run(command,**kwargs):
        kwargs["stdout"].write(b"partial")
        return subprocess.CompletedProcess(command,1,stderr=b"device PRIVATE_TEST_ID disconnected")
    monkeypatch.setattr(phone.subprocess,"run",run)
    with pytest.raises(RuntimeError) as caught:phone.export(Device(),tmp_path/"export")
    assert "PRIVATE_TEST_ID" not in str(caught.value)
    assert (tmp_path/"export/original_export.tar").read_bytes()==b"partial"
