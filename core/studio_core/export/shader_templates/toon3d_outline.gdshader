shader_type spatial;
// Inverted hull: back faces pushed out along the normal and drawn alone, so only a rim of them shows around the model.
render_mode unshaded, cull_front;

uniform vec4 outline_color : source_color = vec4(0.05, 0.05, 0.07, 1.0);
uniform float width = 0.01;
uniform float smooth_normals : hint_range(0.0, 1.0) = 0.35;
uniform vec3 center = vec3(0.0);

void vertex() {
	// Split (hard-edge) normals tear the hull open; blending in the direction from the centre closes the gaps.
	vec3 n = normalize(mix(normalize(NORMAL), normalize(VERTEX - center), smooth_normals));
	VERTEX += n * width;
}

void fragment() {
	ALBEDO = outline_color.rgb;
}
