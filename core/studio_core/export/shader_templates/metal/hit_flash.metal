[[ stitchable ]] half4 assetStudioHitFlash(float2 position, half4 color, float uTime, half4 uColor, float uAmount,
                                           float uSpeed) {
  float4 c = unpre(float4(color));
  float blink = uSpeed > 0.0 ? step(0.5, fract(uTime * uSpeed)) : 0.0;
  float k = max(uAmount, blink) * float(uColor.a);
  return half4(pre(mix(c.rgb, float3(uColor.rgb), k), c.a));
}
