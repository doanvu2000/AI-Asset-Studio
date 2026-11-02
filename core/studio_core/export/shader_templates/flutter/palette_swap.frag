#version 460 core
#include <flutter/runtime_effect.glsl>
precision highp float;

// uRect: where the sprite is drawn (x, y, w, h); uSrc: its cell in the sheet (px); uImage: sheet size (px).
uniform vec4 uRect;
uniform vec4 uSrc;
uniform vec2 uImage;
uniform float uTime;
uniform float uRow;
uniform float uRows;
uniform float uStrength;
uniform sampler2D uTexture;
uniform sampler2D uRamp;
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
  float l = clamp(dot(c.rgb, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
  vec3 r = texture(uRamp, vec2((l * 255.0 + 0.5) / 256.0, (uRow + 0.5) / max(uRows, 1.0))).rgb;
  fragColor = pre(mix(c.rgb, r, uStrength), c.a);
}
