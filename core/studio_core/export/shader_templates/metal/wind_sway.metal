[[ stitchable ]] half4 assetStudioWindSway(float2 position, SwiftUI::Layer layer, float4 uBounds, float uTime,
                                           float uStrength, float uSpeed, float uWave, float uWorldX) {
  float h = max(uBounds.y + uBounds.w - position.y, 0.0);
  float off = sin(uTime * uSpeed * TAU + uWorldX * uWave) * uStrength * h;
  return half4(tap(layer, position - float2(off, 0.0), uBounds));
}
