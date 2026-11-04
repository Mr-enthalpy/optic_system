package org.udc.rawprobe;

import android.graphics.*;
import android.hardware.camera2.*;
import android.hardware.camera2.params.*;
import android.util.*;
import org.json.*;
import java.lang.reflect.Array;
import java.util.*;

/** All exposed keys are retained; unrepresentable vendor values are explicitly flagged. */
final class MetadataJson {
    private MetadataJson() {}
    static Object encode(Object x) throws JSONException {
        if (x == null) return JSONObject.NULL;
        if (x instanceof Boolean || x instanceof String || x instanceof Integer || x instanceof Long
                || x instanceof Short || x instanceof Byte) return x;
        if (x instanceof Float || x instanceof Double) {
            double v=((Number)x).doubleValue(); return Double.isFinite(v)?x:String.valueOf(x);
        }
        if (x.getClass().isArray()) {
            JSONArray a=new JSONArray(); for(int i=0;i<Array.getLength(x);i++) a.put(encode(Array.get(x,i))); return a;
        }
        if (x instanceof Rational) {
            Rational r=(Rational)x; return new JSONObject().put("numerator",r.getNumerator()).put("denominator",r.getDenominator());
        }
        if (x instanceof Rect) { Rect r=(Rect)x; return new JSONObject().put("left",r.left).put("top",r.top).put("right",r.right).put("bottom",r.bottom); }
        if (x instanceof Size) { Size s=(Size)x; return new JSONObject().put("width",s.getWidth()).put("height",s.getHeight()); }
        if (x instanceof SizeF) { SizeF s=(SizeF)x; return new JSONObject().put("width",s.getWidth()).put("height",s.getHeight()); }
        if (x instanceof Point) { Point s=(Point)x; return new JSONObject().put("x",s.x).put("y",s.y); }
        if (x instanceof PointF) { PointF s=(PointF)x; return new JSONObject().put("x",s.x).put("y",s.y); }
        if (x instanceof Range) { Range<?> r=(Range<?>)x; return new JSONObject().put("lower",encode(r.getLower())).put("upper",encode(r.getUpper())); }
        if (x instanceof Pair) { Pair<?,?> r=(Pair<?,?>)x; return new JSONArray().put(encode(r.first)).put(encode(r.second)); }
        if (x instanceof BlackLevelPattern) {
            int[] v=new int[4]; ((BlackLevelPattern)x).copyTo(v,0); return encode(v);
        }
        if (x instanceof RggbChannelVector) {
            RggbChannelVector r=(RggbChannelVector)x;
            return new JSONObject().put("channel_order","R,Geven,Godd,B").put("values",new JSONArray().put(r.getRed()).put(r.getGreenEven()).put(r.getGreenOdd()).put(r.getBlue()));
        }
        if (x instanceof ColorSpaceTransform) { Rational[] v=new Rational[9]; ((ColorSpaceTransform)x).copyElements(v,0); return encode(v); }
        if (x instanceof LensShadingMap) {
            LensShadingMap m=(LensShadingMap)x; float[] v=new float[m.getGainFactorCount()];m.copyGainFactors(v,0);
            return new JSONObject().put("rows",m.getRowCount()).put("columns",m.getColumnCount()).put("channel_order","R,Geven,Godd,B").put("values",encode(v));
        }
        if (x instanceof TonemapCurve) {
            TonemapCurve t=(TonemapCurve)x; JSONObject j=new JSONObject();
            for(int c=0;c<3;c++){ float[] v=new float[2*t.getPointCount(c)];t.copyColorCurve(c,v,0);j.put(String.valueOf(c),encode(v)); } return j;
        }
        if (x instanceof MeteringRectangle) { MeteringRectangle r=(MeteringRectangle)x; return new JSONObject().put("rect",encode(r.getRect())).put("weight",r.getMeteringWeight()); }
        if (x instanceof Face) { Face f=(Face)x;return new JSONObject().put("id",f.getId()).put("score",f.getScore()).put("bounds",encode(f.getBounds())).put("left_eye",encode(f.getLeftEyePosition())).put("right_eye",encode(f.getRightEyePosition())).put("mouth",encode(f.getMouthPosition())); }
        if (x instanceof OisSample) { OisSample o=(OisSample)x;return new JSONObject().put("timestamp_ns",o.getTimestamp()).put("x_shift",o.getXshift()).put("y_shift",o.getYshift()); }
        if (x instanceof StreamConfigurationMap) {
            StreamConfigurationMap m=(StreamConfigurationMap)x; JSONObject j=new JSONObject();JSONArray outs=new JSONArray(),ins=new JSONArray();
            for(int f:m.getOutputFormats()) {
                Size[] sizes=m.getOutputSizes(f);
                if(sizes!=null) for(Size s:sizes) outs.put(new JSONObject().put("format",f).put("size",encode(s)).put("min_frame_duration_ns",m.getOutputMinFrameDuration(f,s)).put("stall_duration_ns",m.getOutputStallDuration(f,s)));
                Size[] high=m.getHighResolutionOutputSizes(f);
                if(high!=null) for(Size s:high) outs.put(new JSONObject().put("format",f).put("size",encode(s)).put("high_resolution",true).put("min_frame_duration_ns",m.getOutputMinFrameDuration(f,s)).put("stall_duration_ns",m.getOutputStallDuration(f,s)));
            }
            for(int f:m.getInputFormats()) ins.put(new JSONObject().put("format",f).put("sizes",encode(m.getInputSizes(f))).put("valid_outputs",encode(m.getValidOutputFormatsForInput(f))));
            JSONArray hs=new JSONArray(); for(Size size:m.getHighSpeedVideoSizes()) hs.put(new JSONObject().put("size",encode(size)).put("fps_ranges",encode(m.getHighSpeedVideoFpsRangesFor(size))));
            return j.put("outputs",outs).put("inputs",ins).put("high_speed_video",hs);
        }
        if (x instanceof Collection) { JSONArray a=new JSONArray();for(Object v:(Collection<?>)x)a.put(encode(v));return a; }
        if (x instanceof CameraCharacteristics.Key) return ((CameraCharacteristics.Key<?>)x).getName();
        if (x instanceof CaptureRequest.Key) return ((CaptureRequest.Key<?>)x).getName();
        if (x instanceof CaptureResult.Key) return ((CaptureResult.Key<?>)x).getName();
        // Do not silently claim that an opaque vendor object was fully serialized.
        return new JSONObject().put("serialization_status","opaque_value_not_lossless").put("java_class",x.getClass().getName()).put("text",String.valueOf(x));
    }
    static JSONObject characteristics(CameraCharacteristics c) throws JSONException {
        JSONObject j=new JSONObject(),errors=new JSONObject();
        for(CameraCharacteristics.Key<?> k:c.getKeys()) try{j.put(k.getName(),encode(c.get(k)));}catch(Exception e){j.put(k.getName(),JSONObject.NULL);errors.put(k.getName(),e.toString());}
        return new JSONObject().put("values",j).put("errors",errors);
    }
    static JSONObject result(CaptureResult r) throws JSONException {
        JSONObject j=new JSONObject(),errors=new JSONObject();
        for(CaptureResult.Key<?> k:r.getKeys())try{j.put(k.getName(),encode(r.get(k)));}catch(Exception e){j.put(k.getName(),JSONObject.NULL);errors.put(k.getName(),e.toString());}
        JSONObject root=new JSONObject().put("values",j).put("errors",errors).put("frame_number",r.getFrameNumber()).put("sequence_id",r.getSequenceId());
        if(r instanceof TotalCaptureResult){
            TotalCaptureResult t=(TotalCaptureResult)r;JSONObject physical=new JSONObject();
            for(Map.Entry<String,CaptureResult> e:t.getPhysicalCameraResults().entrySet())physical.put(e.getKey(),result(e.getValue()));
            JSONArray partials=new JSONArray();for(CaptureResult v:t.getPartialResults())partials.put(result(v));
            root.put("physical_results",physical).put("partial_results",partials);
        }
        return root;
    }
    static JSONObject request(CaptureRequest r) throws JSONException {
        JSONObject j=new JSONObject(),errors=new JSONObject();
        for(CaptureRequest.Key<?> k:r.getKeys())try{j.put(k.getName(),encode(r.get(k)));}catch(Exception e){errors.put(k.getName(),e.toString());}
        return new JSONObject().put("values",j).put("errors",errors);
    }
}
