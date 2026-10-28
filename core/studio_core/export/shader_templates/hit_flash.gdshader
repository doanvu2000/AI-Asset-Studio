// AI Asset Studio — hit flash. Tween `amount` 1 -> 0 on hit; speed > 0 blinks on its own.
shader_type canvas_item;

uniform vec4 flash_color : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform float speed : hint_range(0.0, 20.0) = 0.0;

void fragment() {
	vec4 c = texture(TEXTURE, UV) * COLOR;
	float blink = speed > 0.0 ? step(0.5, fract(TIME * speed)) : 0.0;
	float k = max(amount, blink) * flash_color.a;
	COLOR = vec4(mix(c.rgb, flash_color.rgb, k), c.a);
}
