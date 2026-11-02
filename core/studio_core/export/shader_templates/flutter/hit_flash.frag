#version 460 core
#include <flutter/runtime_effect.glsl>
precision highp float;

// uRect: where the sprite is drawn (x, y, w, h); uSrc: its cell in the sheet (px); uImage: sheet size (px).
uniform vec4 uRect;
uniform vec4 uSrc;
uniform vec2 uImage;
uniform float uTime;
uniform vec4 uColor;
uniform float uAmount;
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

void main() {
  vec4 c = unpre(tap(srcPx(FlutterFragCoord().xy)));
  float blink = uSpeed > 0.0 ? step(0.5, fract(uTime * uSpeed)) : 0.0;
  float k = max(uAmount, blink) * uColor.a;
  fragColor = pre(mix(c.rgb, uColor.rgb, k), c.a);
}
