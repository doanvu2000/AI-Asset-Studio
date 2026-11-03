#include <metal_stdlib>
#include <SwiftUI/SwiftUI_Metal.h>
using namespace metal;

constant float TAU = 6.28318530718;

// SwiftUI hands over premultiplied colour and expects premultiplied colour back.
static float4 unpre(float4 c) {
  return c.a > 0.0001 ? float4(c.rgb / c.a, c.a) : float4(0.0);
}

static float4 pre(float3 rgb, float a) {
  return float4(rgb * a, a);
}

// bounds = (x, y, width, height) from .boundingRect; positions are in the view's top-left, y-down space like Flutter.
static float4 tap(SwiftUI::Layer layer, float2 p, float4 bounds) {
  if (p.x < bounds.x || p.y < bounds.y || p.x >= bounds.x + bounds.z || p.y >= bounds.y + bounds.w) {
    return float4(0.0);
  }
  return float4(layer.sample(p));
}
