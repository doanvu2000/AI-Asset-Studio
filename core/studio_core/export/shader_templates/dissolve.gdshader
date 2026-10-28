// AI Asset Studio — noise dissolve. Tween `progress` 0 -> 1; speed > 0 loops it on its own.
shader_type canvas_item;

uniform float progress : hint_range(0.0, 1.0) = 0.0;
uniform vec4 edge_color : source_color = vec4(1.0, 0.55, 0.1, 1.0);
uniform float edge_width : hint_range(0.0, 0.5) = 0.06;
uniform float noise_scale : hint_range(1.0, 64.0) = 6.0;
uniform float speed : hint_range(0.0, 20.0) = 0.0;

float hash21(vec2 p) {
	p = fract(p * vec2(123.34, 456.21));
	p += dot(p, p + 45.32);
	return fract(p.x * p.y);
}

float value_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	float a = hash21(i);
	float b = hash21(i + vec2(1.0, 0.0));
	float c = hash21(i + vec2(0.0, 1.0));
	float d = hash21(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

void fragment() {
	vec4 c = COLOR;
	vec2 px = UV / TEXTURE_PIXEL_SIZE / noise_scale;
	float n = value_noise(px) * 0.65 + value_noise(px * 2.0 + 17.0) * 0.35;
	float p = speed > 0.0 ? abs(fract(TIME * speed * 0.5) * 2.0 - 1.0) : progress;
	float d = n - mix(-edge_width, 1.0, p);
	float keep = step(0.0, d);
	float edge = keep * (1.0 - step(edge_width, d));
	COLOR = vec4(mix(c.rgb, edge_color.rgb, edge * edge_color.a), c.a * keep);
}
