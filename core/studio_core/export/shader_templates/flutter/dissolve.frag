#version 460 core
#include <flutter/runtime_effect.glsl>
precision highp float;

// uRect: where the sprite is drawn (x, y, w, h); uSrc: its cell in the sheet (px); uImage: sheet size (px).
uniform vec4 uRect;
uniform vec4 uSrc;
uniform vec2 uImage;
uniform float uTime;
uniform vec4 uEdgeColor;
uniform float uProgress;
uniform float uEdgeWidth;
uniform float uNoiseScale;
uniform float uSpeed;
uniform sampler2D uTexture;

layout(location = 0) out vec4 fragColor;

const float TAU = 6.28318530718;

vec2 srcPx(vec2 frag) {
  return uSrc.xy + (frag - uRect.xy) / uRect.zw * uSrc.zw;
}

// Outside the cell reads as transparent so effects never pull in the neighbouring frame.
vec4 tap(vec2 p) {
  vec2 hi = uSrc.xy + uSrc.zw;
  if (p.x < uSrc.x || p.y < uSrc.y || p.x >= hi.x || p.y >= hi.y) {
    return vec4(0.0);
  }
  return texture(uTexture, p / uImage);
}

// Flutter samples premultiplied colour and expects premultiplied output.
vec4 unpre(vec4 c) {
  return c.a > 0.0001 ? vec4(c.rgb / c.a, c.a) : vec4(0.0);
}

vec4 pre(vec3 rgb, float a) {
  return vec4(rgb * a, a);
}

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float valueNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash21(i);
  float b = hash21(i + vec2(1.0, 0.0));
  float c = hash21(i + vec2(0.0, 1.0));
  float d = hash21(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void main() {
  vec2 p = srcPx(FlutterFragCoord().xy);
  vec4 c = unpre(tap(p));
  vec2 q = (p - uSrc.xy) / max(uNoiseScale, 1.0);
  float n = valueNoise(q) * 0.65 + valueNoise(q * 2.0 + 17.0) * 0.35;
  float prog = uSpeed > 0.0 ? abs(fract(uTime * uSpeed * 0.5) * 2.0 - 1.0) : uProgress;
  float d = n - mix(-uEdgeWidth, 1.0, prog);
  float keep = step(0.0, d);
  float edge = keep * (1.0 - step(uEdgeWidth, d));
  fragColor = pre(mix(c.rgb, uEdgeColor.rgb, edge * uEdgeColor.a), c.a * keep);
}
