// AI Asset Studio — wind sway, anchored at the node origin (the sprite's foot): the top moves strength × height.
shader_type canvas_item;

uniform float strength : hint_range(0.0, 1.0) = 0.06;
uniform float speed : hint_range(0.0, 20.0) = 0.6;
uniform float wave = 0.02;

void vertex() {
	float h = max(-VERTEX.y, 0.0);
	float phase = (MODEL_MATRIX * vec4(0.0, 0.0, 0.0, 1.0)).x * wave;
	VERTEX.x += sin(TIME * speed * TAU + phase) * strength * h;
}
