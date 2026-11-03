[[ stitchable ]] half4 assetStudioWaterRipple(float2 position, SwiftUI::Layer layer, float4 uBounds, float uTime,
                                              half4 uTint, float uAmplitude, float uWavelength, float uSpeed) {
  float s = sin(((position.y - uBounds.y) / max(uWavelength, 1.0) + uTime * uSpeed) * TAU);
  float4 c = unpre(tap(layer, position + float2(s * uAmplitude, 0.0), uBounds));
  return half4(pre(c.rgb * float3(uTint.rgb), c.a * float(uTint.a)));
}
