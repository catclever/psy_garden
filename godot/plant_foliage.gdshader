shader_type canvas_item;

uniform vec4 tint_color : source_color = vec4(1.0);
uniform float root_uv_y = 1.0;
uniform float occlusion_height = 0.0;
uniform float occlusion_darken = 0.0;
uniform vec3 shadow_ambient_color : source_color = vec3(0.28, 0.34, 0.42);

void fragment() {
	vec4 tex_color = texture(TEXTURE, UV);
	vec3 base_color = tex_color.rgb * tint_color.rgb;
	float alpha = tex_color.a;

	// Canopy shadow reception
	float h_norm = clamp((root_uv_y - UV.y) / max(root_uv_y, 0.01), 0.0, 1.0);
	float in_shadow = 0.0;
	if (occlusion_darken > 0.01) {
		in_shadow = 1.0 - smoothstep(occlusion_height - 0.15, occlusion_height + 0.10, h_norm);
	}

	vec3 darkened_color = mix(base_color, base_color * shadow_ambient_color, in_shadow * occlusion_darken);

	COLOR = vec4(darkened_color, alpha);
}

void light() {
	float h_norm = clamp((root_uv_y - UV.y) / max(root_uv_y, 0.01), 0.0, 1.0);
	float in_shadow = 0.0;
	if (occlusion_darken > 0.01) {
		in_shadow = 1.0 - smoothstep(occlusion_height - 0.15, occlusion_height + 0.10, h_norm);
	}

	float shadow_atten = 1.0 - in_shadow * occlusion_darken * 0.5;
	LIGHT = vec4(COLOR.rgb * LIGHT_COLOR.rgb * LIGHT_ENERGY * shadow_atten, LIGHT_COLOR.a);
}
