[[ stitchable ]] half4 assetStudioOutlinePulse(float2 position, SwiftUI::Layer layer, float4 uBounds, float uTime,
                                               half4 uColor, float uWidth, float uSpeed, float uMinAlpha) {
  float4 c = unpre(tap(layer, position, uBounds));
  float2 d = float2(uWidth);
  float2 e = d * 0.7071;
  float a = 0.0;
  a = max(a, tap(layer, position + float2(d.x, 0.0), uBounds).a);
  a = max(a, tap(layer, position - float2(d.x, 0.0), uBounds).a);
  a = max(a, tap(layer, position + float2(0.0, d.y), uBounds).a);
  a = max(a, tap(layer, position - float2(0.0, d.y), uBounds).a);
  a = max(a, tap(layer, position + e, uBounds).a);
  a = max(a, tap(layer, position - e, uBounds).a);
  a = max(a, tap(layer, position + float2(e.x, -e.y), uBounds).a);
  a = max(a, tap(layer, position + float2(-e.x, e.y), uBounds).a);
  float pulse = mix(uMinAlpha, 1.0, 0.5 + 0.5 * sin(uTime * uSpeed * TAU));
  float oa = clamp(a - c.a, 0.0, 1.0) * float(uColor.a) * pulse;
  float outA = c.a + oa * (1.0 - c.a);
  return half4(float4(c.rgb * c.a + float3(uColor.rgb) * oa * (1.0 - c.a), outA));
}
