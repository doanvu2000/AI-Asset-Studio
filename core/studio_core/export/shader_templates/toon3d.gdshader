shader_type spatial;
render_mode diffuse_lambert, specular_disabled;

uniform sampler2D albedo_tex : source_color, hint_default_white, filter_linear_mipmap, repeat_enable;
uniform vec4 albedo_color : source_color = vec4(1.0);
uniform sampler2D ramp : source_color, filter_nearest, repeat_disable;
uniform vec4 rim_color : source_color = vec4(1.0);
uniform float rim_strength : hint_range(0.0, 1.0) = 0.35;
uniform float rim_threshold : hint_range(0.0, 1.0) = 0.72;

void fragment() {
	ALBEDO = texture(albedo_tex, UV).rgb * albedo_color.rgb;
	float rim = 1.0 - clamp(dot(normalize(NORMAL), normalize(VIEW)), 0.0, 1.0);
	EMISSION = rim_color.rgb * rim_strength * step(rim_threshold, rim);
}

void light() {
	float ndl = clamp(dot(NORMAL, LIGHT), 0.0, 0.999);
	DIFFUSE_LIGHT += LIGHT_COLOR * texture(ramp, vec2(ndl, 0.5)).rgb * ATTENUATION / PI;
}
