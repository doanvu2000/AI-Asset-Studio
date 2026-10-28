// AI Asset Studio — palette swap: each pixel's luma picks a colour from row `row` of the ramp (256 px wide).
shader_type canvas_item;

uniform sampler2D ramp : source_color, filter_nearest, repeat_disable;
uniform float row = 0.0;
uniform float rows = 1.0;
uniform float strength : hint_range(0.0, 1.0) = 1.0;

varying vec4 modulate;

void vertex() {
	modulate = COLOR;
}

void fragment() {
	vec4 c = texture(TEXTURE, UV);
	float l = clamp(dot(c.rgb, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
	vec3 r = texture(ramp, vec2((l * 255.0 + 0.5) / 256.0, (row + 0.5) / max(rows, 1.0))).rgb;
	COLOR = vec4(mix(c.rgb, r, strength), c.a) * modulate;
}
