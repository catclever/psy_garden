shader_type canvas_item;

uniform sampler2D tRed : filter_linear, hint_default_transparent;
uniform sampler2D tGreen : filter_linear, hint_default_transparent;
uniform sampler2D tBlue : filter_linear, hint_default_transparent;

uniform float root_uv_y = 1.0;
uniform vec4 shadow_tint : source_color = vec4(0.0, 0.0, 0.0, 0.72);

void fragment() {
    float aRed = texture(tRed, UV).a;
    float aGreen = texture(tGreen, UV).a;
    float aBlue = texture(tBlue, UV).a;

    float a = max(aRed, max(aGreen, aBlue));

    float dist_from_root = clamp((root_uv_y - UV.y) / max(root_uv_y, 0.01), 0.0, 1.0);
    float fade = pow(1.0 - dist_from_root * 0.82, 1.35);

    COLOR = vec4(shadow_tint.rgb, shadow_tint.a * a * fade);
}
