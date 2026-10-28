// AI Asset Studio — horizontal water ripple (UV wobble in pixels) with a tint.
shader_type canvas_item;

uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform float amplitude : hint_range(0.0, 8.0) = 1.5;
uniform float wavelength : hint_range(1.0, 128.0) = 12.0;
uniform float speed : hint_range(0.0, 20.0) = 1.0;

varying vec4 modulate;

void vertex() {
	modulate = COLOR;
}

void fragment() {
	vec2 px = UV / TEXTURE_PIXEL_SIZE;
	float s = sin((px.y / wavelength + TIME * speed) * TAU);
	vec2 uv = UV + vec2(s * amplitude * TEXTURE_PIXEL_SIZE.x, 0.0);
	COLOR = texture(TEXTURE, uv) * tint * modulate;
}
