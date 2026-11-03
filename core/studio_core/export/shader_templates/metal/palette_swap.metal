// A colorEffect cannot take a ramp texture, so the palette arrives as flat RGB stops (dark to light, uRows rows) and
// is interpolated here exactly like the ramp bitmap the other targets bake.
[[ stitchable ]] half4 assetStudioPaletteSwap(float2 position, half4 color, float uTime, float uRow, float uRows,
                                              float uStrength, device const float *uPalette, int uPaletteCount) {
  float4 c = unpre(float4(color));
  float l = clamp(dot(c.rgb, float3(0.299, 0.587, 0.114)), 0.0, 1.0);
  int rows = max(int(uRows), 1);
  int stops = max(uPaletteCount / 3 / rows, 1);
  int row = clamp(int(uRow), 0, rows - 1);
  float t = l * float(stops - 1);
  int i = min(int(floor(t)), stops - 1);
  int j = min(i + 1, stops - 1);
  int a = (row * stops + i) * 3;
  int b = (row * stops + j) * 3;
  float3 ca = float3(uPalette[a], uPalette[a + 1], uPalette[a + 2]);
  float3 cb = float3(uPalette[b], uPalette[b + 1], uPalette[b + 2]);
  float3 r = mix(ca, cb, t - float(i));
  return half4(pre(mix(c.rgb, r, uStrength), c.a));
}
