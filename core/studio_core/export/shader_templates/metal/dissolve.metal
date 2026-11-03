static float hash21(float2 p) {
  p = fract(p * float2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

static float valueNoise(float2 p) {
  float2 i = floor(p);
  float2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash21(i);
  float b = hash21(i + float2(1.0, 0.0));
  float c = hash21(i + float2(0.0, 1.0));
  float d = hash21(i + float2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

[[ stitchable ]] half4 assetStudioDissolve(float2 position, half4 color, float uTime, half4 uEdgeColor, float uProgress,
                                           float uEdgeWidth, float uNoiseScale, float uSpeed) {
  float4 c = unpre(float4(color));
  float2 q = position / max(uNoiseScale, 1.0);
  float n = valueNoise(q) * 0.65 + valueNoise(q * 2.0 + 17.0) * 0.35;
  float prog = uSpeed > 0.0 ? abs(fract(uTime * uSpeed * 0.5) * 2.0 - 1.0) : uProgress;
  float d = n - mix(-uEdgeWidth, 1.0, prog);
  float keep = step(0.0, d);
  float edge = keep * (1.0 - step(uEdgeWidth, d));
  return half4(pre(mix(c.rgb, float3(uEdgeColor.rgb), edge * float(uEdgeColor.a)), c.a * keep));
}
