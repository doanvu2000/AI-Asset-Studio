#version 460 core
#include <flutter/runtime_effect.glsl>
precision highp float;

// uRect: where the sprite is drawn (x, y, w, h); uSrc: its cell in the sheet (px); uImage: sheet size (px).
uniform vec4 uRect;
uniform vec4 uSrc;
uniform vec2 uImage;
uniform float uTime;
uniform vec4 uColor;
uniform float uWidth;
uniform float uSpeed;
uniform float uMinAlpha;
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

void main() {
  vec2 p = srcPx(FlutterFragCoord().xy);
  vec4 c = unpre(tap(p));
  vec2 d = vec2(uWidth);
  vec2 e = d * 0.7071;
  float a = 0.0;
  a = max(a, tap(p + vec2(d.x, 0.0)).a);
  a = max(a, tap(p - vec2(d.x, 0.0)).a);
  a = max(a, tap(p + vec2(0.0, d.y)).a);
  a = max(a, tap(p - vec2(0.0, d.y)).a);
  a = max(a, tap(p + e).a);
  a = max(a, tap(p - e).a);
  a = max(a, tap(p + vec2(e.x, -e.y)).a);
  a = max(a, tap(p + vec2(-e.x, e.y)).a);
  float pulse = mix(uMinAlpha, 1.0, 0.5 + 0.5 * sin(uTime * uSpeed * TAU));
  float oa = clamp(a - c.a, 0.0, 1.0) * uColor.a * pulse;
  float outA = c.a + oa * (1.0 - c.a);
  fragColor = vec4(c.rgb * c.a + uColor.rgb * oa * (1.0 - c.a), outA);
}
