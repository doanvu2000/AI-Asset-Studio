#version 460 core
#include <flutter/runtime_effect.glsl>
precision highp float;

// uRect: where the sprite is drawn (x, y, w, h); uSrc: its cell in the sheet (px); uImage: sheet size (px).
uniform vec4 uRect;
uniform vec4 uSrc;
uniform vec2 uImage;
uniform float uTime;
uniform vec4 uAmbient;
uniform float uHasNormal;
uniform vec4 uLightPos0;
uniform vec4 uLightCol0;
uniform vec4 uLightPos1;
uniform vec4 uLightCol1;
uniform vec4 uLightPos2;
uniform vec4 uLightCol2;
uniform vec4 uLightPos3;
uniform vec4 uLightCol3;
uniform sampler2D uTexture;
uniform sampler2D uNormal;
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

// pos: (x, y, height, radius) in drawn-rect pixels; col: (rgb, intensity). Normal G is up (OpenGL), screen y is down.
vec3 shade(vec3 n, vec2 frag, vec4 pos, vec4 col) {
  vec2 d = pos.xy - frag;
  float att = clamp(1.0 - length(d) / max(pos.w, 1.0), 0.0, 1.0);
  vec3 l = normalize(vec3(d.x, -d.y, max(pos.z, 1.0)));
  return col.rgb * col.a * max(dot(n, l), 0.0) * att * att;
}

void main() {
  vec2 frag = FlutterFragCoord().xy;
  vec2 p = srcPx(frag);
  vec4 c = unpre(tap(p));
  vec3 n = vec3(0.0, 0.0, 1.0);
  if (uHasNormal > 0.5) {
    vec3 raw = unpre(texture(uNormal, p / uImage)).rgb * 2.0 - 1.0;
    if (dot(raw, raw) > 0.01) {
      n = normalize(raw);
    }
  }
  vec3 light = uAmbient.rgb * uAmbient.a;
  light += shade(n, frag, uLightPos0, uLightCol0);
  light += shade(n, frag, uLightPos1, uLightCol1);
  light += shade(n, frag, uLightPos2, uLightCol2);
  light += shade(n, frag, uLightPos3, uLightCol3);
  fragColor = pre(clamp(c.rgb * light, 0.0, 1.0), c.a);
}
