// AI Asset Studio — pulsing outline drawn from the alpha edge (needs `width` px of empty border around each frame).
shader_type canvas_item;

uniform vec4 outline_color : source_color = vec4(1.0, 0.85, 0.3, 1.0);
uniform float width : hint_range(0.0, 8.0) = 1.0;
uniform float speed : hint_range(0.0, 20.0) = 1.5;
uniform float min_alpha : hint_range(0.0, 1.0) = 0.25;

void fragment() {
	vec4 c = texture(TEXTURE, UV);
	vec2 d = TEXTURE_PIXEL_SIZE * width;
	float a = 0.0;
	a = max(a, texture(TEXTURE, UV + vec2(d.x, 0.0)).a);
	a = max(a, texture(TEXTURE, UV - vec2(d.x, 0.0)).a);
	a = max(a, texture(TEXTURE, UV + vec2(0.0, d.y)).a);
	a = max(a, texture(TEXTURE, UV - vec2(0.0, d.y)).a);
	a = max(a, texture(TEXTURE, UV + d * 0.7071).a);
	a = max(a, texture(TEXTURE, UV - d * 0.7071).a);
	a = max(a, texture(TEXTURE, UV + vec2(d.x, -d.y) * 0.7071).a);
	a = max(a, texture(TEXTURE, UV + vec2(-d.x, d.y) * 0.7071).a);
	float pulse = mix(min_alpha, 1.0, 0.5 + 0.5 * sin(TIME * speed * TAU));
	float oa = clamp(a - c.a, 0.0, 1.0) * outline_color.a * pulse;
	float out_a = c.a + oa * (1.0 - c.a);
	vec3 rgb = (c.rgb * c.a + outline_color.rgb * oa * (1.0 - c.a)) / max(out_a, 0.0001);
	COLOR = vec4(rgb, out_a) * COLOR;
}
