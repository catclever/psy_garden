extends Node2D

const TRANSITION_SNAP: int = 0
const TRANSITION_CROSSFADE: int = 1
const TRANSITION_POP: int = 2

@export var plant_id: String = "plant_0000"

var root_anchor: Vector2 = Vector2.ZERO
var root_uv_y: float = 1.0

var visual_width: float = 100.0
var visual_height: float = 100.0

var desired_height_px: float = -1.0
var meta_root_width: float = -1.0
var fp_w: float = 24.0
var fp_h: float = 14.0
var perspective_scale: float = 1.0
var ground_pitch_ratio: float = 0.55
var courtyard_pos: Vector2 = Vector2.ZERO
var has_courtyard_pos: bool = false
var target_xray_alpha: float = 1.0
var current_xray_alpha: float = 1.0
var _cached_base_scale: float = 1.0
var _cached_raw_w: float = 100.0
var _cached_raw_h: float = 100.0
var _cached_base_rw: float = 20.0
var _textures_ready: bool = false

var is_turntable: bool = false
var is_video_atlas: bool = false
var video_atlas_tex: Texture2D = null
var video_hframes: int = 6
var video_vframes: int = 6
var video_frame_count: int = 36
var current_video_frame: int = 0
var base_yaw_deg: float = 0.0
var turntable_sector_count: int = 8
var turntable_transition_mode: int = TRANSITION_CROSSFADE
var turntable_frames: Dictionary = {}
var current_sector_deg: int = 0
var prev_sector_deg: int = 0
var transition_progress: float = 1.0
var sub_sector_norm: float = 0.0
var caption_prefix: String = ""
var caption_label: Label = null

var _prev_red: Sprite2D = null
var _prev_green: Sprite2D = null
var _prev_blue: Sprite2D = null
var _materials_uniqued: bool = false

var show_footprint_ring: bool = true:
	set(value):
		if show_footprint_ring != value:
			show_footprint_ring = value
			queue_redraw()

var skip_auto_load: bool = false

@onready var sprite_red = $RedSprite
@onready var sprite_green = $GreenSprite
@onready var sprite_blue = $BlueSprite

@onready var ground_shadow_plane = $GroundShadowPlane
@onready var contact_shadow = $GroundShadowPlane/ContactShadow
@onready var sun_shadow = $GroundShadowPlane/SunShadowPivot/SunShadowSprite
@onready var lantern_shadow = $GroundShadowPlane/LanternShadowPivot/LanternShadowSprite
@onready var incense_shadow = $GroundShadowPlane/IncenseShadowPivot/IncenseShadowSprite
@onready var occluder = $Occluder

@onready var sun_shadow_pivot = $GroundShadowPlane/SunShadowPivot
@onready var lantern_shadow_pivot = $GroundShadowPlane/LanternShadowPivot
@onready var incense_shadow_pivot = $GroundShadowPlane/IncenseShadowPivot

func _ready():
	_ensure_node_refs()
	if ground_shadow_plane:
		ground_shadow_plane.scale = Vector2(1.0, ground_pitch_ratio)
	if not skip_auto_load and plant_id != "":
		load_plant(plant_id)
	_ensure_node_refs()
	if ground_shadow_plane:
		ground_shadow_plane.scale = Vector2(1.0, ground_pitch_ratio)

func _ensure_node_refs():
	if not sprite_red: sprite_red = get_node_or_null("RedSprite")
	if not sprite_green: sprite_green = get_node_or_null("GreenSprite")
	if not sprite_blue: sprite_blue = get_node_or_null("BlueSprite")
	if not ground_shadow_plane: ground_shadow_plane = get_node_or_null("GroundShadowPlane")
	if not contact_shadow: contact_shadow = get_node_or_null("GroundShadowPlane/ContactShadow")
	if not sun_shadow: sun_shadow = get_node_or_null("GroundShadowPlane/SunShadowPivot/SunShadowSprite")
	if not lantern_shadow: lantern_shadow = get_node_or_null("GroundShadowPlane/LanternShadowPivot/LanternShadowSprite")
	if not incense_shadow: incense_shadow = get_node_or_null("GroundShadowPlane/IncenseShadowPivot/IncenseShadowSprite")
	if not sun_shadow_pivot: sun_shadow_pivot = get_node_or_null("GroundShadowPlane/SunShadowPivot")
	if not lantern_shadow_pivot: lantern_shadow_pivot = get_node_or_null("GroundShadowPlane/LanternShadowPivot")
	if not incense_shadow_pivot: incense_shadow_pivot = get_node_or_null("GroundShadowPlane/IncenseShadowPivot")

func _ensure_unique_materials():
	if _materials_uniqued:
		return
	_ensure_node_refs()
	for spr in [sprite_red, sprite_green, sprite_blue]:
		if spr and spr.material:
			spr.material = spr.material.duplicate()
	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		if shadow and shadow.material:
			shadow.material = shadow.material.duplicate()
	_materials_uniqued = true

func _ensure_ghost_sprites():
	if _prev_red != null:
		return
	_ensure_node_refs()
	_ensure_unique_materials()

	_prev_red = Sprite2D.new()
	_prev_red.name = "PrevRedSprite"
	_prev_green = Sprite2D.new()
	_prev_green.name = "PrevGreenSprite"
	_prev_blue = Sprite2D.new()
	_prev_blue.name = "PrevBlueSprite"

	var pairs = [
		[_prev_red, sprite_red],
		[_prev_green, sprite_green],
		[_prev_blue, sprite_blue]
	]
	for pair in pairs:
		var ps: Sprite2D = pair[0]
		var src = pair[1]
		ps.centered = false
		ps.visible = false
		if src and src.material:
			ps.material = src.material.duplicate()
		add_child(ps)

	if sprite_red:
		var target_idx: int = sprite_red.get_index()
		move_child(_prev_red, target_idx)
		move_child(_prev_green, target_idx + 1)
		move_child(_prev_blue, target_idx + 2)

func _draw():
	if not show_footprint_ring:
		return
	draw_set_transform(Vector2.ZERO, 0.0, Vector2(1.0, ground_pitch_ratio))
	draw_arc(Vector2.ZERO, fp_w * 0.5, 0.0, TAU, 36, Color(0.55, 0.92, 0.65, 0.52), 1.5, true)
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)

func set_perspective_scale(p_scale: float):
	perspective_scale = clampf(p_scale, 0.35, 2.5)
	if _textures_ready:
		_apply_effective_scale()

func set_ground_pitch(ratio: float):
	ground_pitch_ratio = clampf(ratio, 0.45, 0.92)
	if not ground_shadow_plane:
		ground_shadow_plane = get_node_or_null("GroundShadowPlane")
	if ground_shadow_plane:
		ground_shadow_plane.scale = Vector2(1.0, ground_pitch_ratio)
	if _textures_ready:
		_apply_effective_scale()
	else:
		fp_h = max(6.0, fp_w * ground_pitch_ratio)
		queue_redraw()

func set_xray_alpha(alpha_val: float):
	current_xray_alpha = clampf(alpha_val, 0.2, 1.0)
	for spr in [sprite_red, sprite_green, sprite_blue, _prev_red, _prev_green, _prev_blue]:
		if spr and spr.material:
			(spr.material as ShaderMaterial).set_shader_parameter("xray_alpha", current_xray_alpha)

func set_lab_caption(text_str: String):
	if caption_label == null:
		caption_label = Label.new()
		caption_label.name = "LabCaptionLabel"
		caption_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		caption_label.custom_minimum_size = Vector2(240.0, 22.0)
		caption_label.position = Vector2(-120.0, 10.0)
		caption_label.z_index = 20
		caption_label.add_theme_font_size_override("font_size", 13)
		caption_label.add_theme_constant_override("outline_size", 3)
		caption_label.add_theme_color_override("font_outline_color", Color(0.05, 0.06, 0.08, 0.92))
		caption_label.add_theme_color_override("font_color", Color(0.96, 0.97, 0.92, 1.0))
		add_child(caption_label)
	caption_label.text = text_str
	caption_label.visible = (text_str != "")

func _hide_ghost_sprites():
	for ps in [_prev_red, _prev_green, _prev_blue]:
		if ps:
			ps.visible = false

func _update_turntable_transition_visuals(s: float):
	if is_video_atlas:
		_hide_ghost_sprites()
		if sprite_green:
			sprite_green.position = Vector2.ZERO
			sprite_green.scale = Vector2(s, s)
			if sprite_green.material:
				(sprite_green.material as ShaderMaterial).set_shader_parameter("crossfade_alpha", 1.0)
		return

	if turntable_transition_mode == TRANSITION_SNAP:
		_hide_ghost_sprites()
		for spr in [sprite_red, sprite_green, sprite_blue]:
			if spr:
				spr.position = Vector2.ZERO
				spr.scale = Vector2(s, s)
				if spr.material:
					(spr.material as ShaderMaterial).set_shader_parameter("crossfade_alpha", 1.0)
		return

	if turntable_transition_mode == TRANSITION_CROSSFADE:
		if transition_progress < 0.999 and prev_sector_deg != current_sector_deg and turntable_frames.has(prev_sector_deg):
			var prev_alpha: float = 1.0 - smoothstep(0.35, 1.0, transition_progress)
			var curr_alpha: float = smoothstep(0.0, 0.75, transition_progress)
			var pf: Dictionary = turntable_frames[prev_sector_deg]
			var prev_base_s: float = (desired_height_px / float(pf["crown_h"])) if desired_height_px > 0.0 else 1.0
			var prev_s: float = prev_base_s * clampf(perspective_scale, 0.35, 2.5)

			var pairs = [
				[_prev_red, sprite_red, "tex_red"],
				[_prev_green, sprite_green, "tex_green"],
				[_prev_blue, sprite_blue, "tex_blue"]
			]
			for pair in pairs:
				var prev_spr: Sprite2D = pair[0]
				var act_spr = pair[1]
				var key: String = pair[2]
				if not prev_spr:
					continue
				var ptex: Texture2D = pf.get(key, null)
				if ptex:
					prev_spr.visible = true
					prev_spr.texture = ptex
					prev_spr.centered = false
					prev_spr.offset = -pf["root_anchor"]
					prev_spr.position = Vector2.ZERO
					prev_spr.scale = Vector2(prev_s, prev_s)
					if prev_spr.material:
						var pmat = prev_spr.material as ShaderMaterial
						pmat.set_shader_parameter("root_uv_y", pf["root_uv_y"])
						pmat.set_shader_parameter("crossfade_alpha", prev_alpha)
						pmat.set_shader_parameter("xray_alpha", current_xray_alpha)
						if act_spr and act_spr.material:
							var amat = act_spr.material as ShaderMaterial
							pmat.set_shader_parameter("tint_color", amat.get_shader_parameter("tint_color"))
							pmat.set_shader_parameter("occlusion_height", amat.get_shader_parameter("occlusion_height"))
							pmat.set_shader_parameter("occlusion_darken", amat.get_shader_parameter("occlusion_darken"))
				else:
					prev_spr.visible = false

			for spr in [sprite_red, sprite_green, sprite_blue]:
				if spr:
					spr.position = Vector2.ZERO
					spr.scale = Vector2(s, s)
					if spr.material:
						(spr.material as ShaderMaterial).set_shader_parameter("crossfade_alpha", curr_alpha)
		else:
			_hide_ghost_sprites()
			for spr in [sprite_red, sprite_green, sprite_blue]:
				if spr:
					spr.position = Vector2.ZERO
					spr.scale = Vector2(s, s)
					if spr.material:
						(spr.material as ShaderMaterial).set_shader_parameter("crossfade_alpha", 1.0)
		return

	if turntable_transition_mode == TRANSITION_POP:
		_hide_ghost_sprites()
		var pop_x: float = lerpf(0.84, 1.0, 1.0 - pow(1.0 - transition_progress, 3.0))
		for spr in [sprite_red, sprite_green, sprite_blue]:
			if spr:
				spr.scale = Vector2(s * pop_x, s)
				if spr.material:
					(spr.material as ShaderMaterial).set_shader_parameter("crossfade_alpha", 1.0)
		if sprite_green:
			sprite_green.position = Vector2.ZERO
		if sprite_red:
			sprite_red.position = Vector2(-sub_sector_norm * 3.5 * perspective_scale, 0.0)
		if sprite_blue:
			sprite_blue.position = Vector2(sub_sector_norm * 2.5 * perspective_scale, 0.0)

func _apply_effective_scale():
	var s: float = _cached_base_scale * clampf(perspective_scale, 0.35, 2.5)

	visual_width = _cached_raw_w * s
	visual_height = _cached_raw_h * s

	fp_w = max(10.0, _cached_base_rw * s)
	fp_h = max(6.0, fp_w * ground_pitch_ratio)

	if not ground_shadow_plane:
		ground_shadow_plane = get_node_or_null("GroundShadowPlane")
	if ground_shadow_plane:
		ground_shadow_plane.scale = Vector2(1.0, ground_pitch_ratio)

	if contact_shadow:
		contact_shadow.position = Vector2.ZERO
		var cs: float = clampf(fp_w / 24.0, 0.45, 2.6)
		contact_shadow.scale = Vector2(cs, cs)

	for spr in [sprite_red, sprite_green, sprite_blue]:
		if spr and spr.visible:
			spr.centered = false
			spr.offset = -root_anchor
			spr.scale = Vector2(s, s)

	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		if not shadow:
			continue
		shadow.centered = false
		shadow.offset = -root_anchor
		shadow.scale = Vector2(s, s)

	if is_turntable:
		_update_turntable_transition_visuals(s)

	queue_redraw()

func register_turntable_frame(angle_deg: int, tex_red: Texture2D, tex_green: Texture2D, tex_blue: Texture2D, anchor_uv: Vector2, crown_h_override: float = -1.0):
	_ensure_node_refs()
	var ref_tex = tex_green if tex_green else (tex_red if tex_red else tex_blue)
	if not ref_tex:
		return

	var images: Array = []
	for t in [tex_red, tex_green, tex_blue]:
		if t:
			var img = t.get_image()
			if img:
				if img.is_compressed():
					img.decompress()
				images.append(img)

	if images.is_empty():
		return

	var width: int = images[0].get_width()
	var height: int = images[0].get_height()
	var top_y: int = height
	var bottom_y: int = -1
	var min_x: int = width
	var max_x: int = 0

	for img in images:
		var used: Rect2i = img.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			top_y = min(top_y, used.position.y)
			bottom_y = max(bottom_y, used.position.y + used.size.y - 1)
			min_x = min(min_x, used.position.x)
			max_x = max(max_x, used.position.x + used.size.x - 1)

	if min_x > max_x:
		min_x = 0
		max_x = max(0, width - 1)
	if bottom_y == -1:
		bottom_y = max(0, height - 1)
	if top_y >= bottom_y:
		top_y = 0

	var f_root_anchor: Vector2
	var f_root_uv_y: float
	if anchor_uv.x >= 0.0 and anchor_uv.y > 0.0:
		f_root_anchor = Vector2(
			clampf(anchor_uv.x, 0.05, 0.95) * float(width),
			clampf(anchor_uv.y, 0.60, 1.0) * float(height)
		)
		f_root_uv_y = clampf(anchor_uv.y, 0.05, 1.0)
	else:
		f_root_anchor = Vector2(float(width) * 0.5, float(bottom_y))
		f_root_uv_y = float(bottom_y) / float(max(height, 1))

	var raw_w: float = max(24.0, float(max_x - min_x))
	var crown_h: float = crown_h_override if crown_h_override > 1.0 else max(24.0, f_root_anchor.y - float(top_y))

	turntable_frames[angle_deg] = {
		"angle_deg": angle_deg,
		"tex_red": tex_red,
		"tex_green": tex_green,
		"tex_blue": tex_blue,
		"ref_tex": ref_tex,
		"width": width,
		"height": height,
		"root_anchor": f_root_anchor,
		"root_uv_y": f_root_uv_y,
		"raw_w": raw_w,
		"crown_h": crown_h
	}

	is_turntable = true
	_ensure_ghost_sprites()

	if angle_deg == current_sector_deg or not _textures_ready:
		if not _textures_ready:
			current_sector_deg = angle_deg
			prev_sector_deg = angle_deg
		_apply_turntable_frame(turntable_frames[angle_deg])

func _apply_turntable_frame(frame: Dictionary):
	_ensure_node_refs()
	_ensure_unique_materials()

	root_anchor = frame["root_anchor"]
	root_uv_y = frame["root_uv_y"]
	_cached_raw_w = frame["raw_w"]
	_cached_raw_h = frame["crown_h"]
	_cached_base_scale = (desired_height_px / frame["crown_h"]) if desired_height_px > 0.0 else 1.0
	_cached_base_rw = meta_root_width if meta_root_width > 0.0 else max(10.0, _cached_raw_w * 0.15)
	_textures_ready = true

	var tex_red = frame["tex_red"]
	var tex_green = frame["tex_green"]
	var tex_blue = frame["tex_blue"]
	var ref_tex = frame["ref_tex"]

	var setups = [
		{"sprite": sprite_red, "tex": tex_red},
		{"sprite": sprite_green, "tex": tex_green},
		{"sprite": sprite_blue, "tex": tex_blue}
	]
	for setup in setups:
		var spr = setup.sprite
		var tex = setup.tex
		if not spr:
			continue
		if tex:
			spr.visible = true
			spr.texture = tex
			spr.centered = false
			spr.offset = -root_anchor
			if spr.material:
				(spr.material as ShaderMaterial).set_shader_parameter("root_uv_y", root_uv_y)
		else:
			spr.visible = false
			spr.texture = null

	set_xray_alpha(current_xray_alpha)

	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		if not shadow:
			continue
		shadow.texture = ref_tex
		shadow.centered = false
		shadow.offset = -root_anchor
		if shadow.material:
			var smat = shadow.material as ShaderMaterial
			smat.set_shader_parameter("tRed", tex_red)
			smat.set_shader_parameter("tGreen", tex_green)
			smat.set_shader_parameter("tBlue", tex_blue)
			smat.set_shader_parameter("root_uv_y", root_uv_y)

	_apply_effective_scale()

func setup_video_atlas_turntable(id: String, atlas_tex: Texture2D, hframes: int, vframes: int, frame_count: int, anchor_px: Vector2, crown_h: float, target_h_px: float, root_w_px: float):
	_ensure_node_refs()
	_ensure_unique_materials()
	_hide_ghost_sprites()
	if not atlas_tex:
		push_error("Missing video atlas texture for " + id)
		return

	plant_id = id
	is_turntable = true
	is_video_atlas = true
	video_atlas_tex = atlas_tex
	video_hframes = max(1, hframes)
	video_vframes = max(1, vframes)
	video_frame_count = max(1, frame_count)
	current_video_frame = 0
	current_sector_deg = 0
	prev_sector_deg = 0
	transition_progress = 1.0

	var cell_w: float = float(atlas_tex.get_width()) / float(video_hframes)
	var cell_h: float = float(atlas_tex.get_height()) / float(video_vframes)

	root_anchor = anchor_px
	root_uv_y = clampf(anchor_px.y / max(cell_h, 1.0), 0.05, 1.0)
	desired_height_px = target_h_px
	meta_root_width = root_w_px

	_cached_raw_w = max(24.0, cell_w)
	_cached_raw_h = max(24.0, crown_h)
	_cached_base_scale = (target_h_px / _cached_raw_h) if target_h_px > 0.0 else 1.0
	_cached_base_rw = root_w_px if root_w_px > 0.0 else 22.0
	_textures_ready = true

	var init_uv_range := Vector2(0.0, 1.0 / float(video_vframes))

	if sprite_red:
		sprite_red.visible = false
		sprite_red.texture = null
	if sprite_blue:
		sprite_blue.visible = false
		sprite_blue.texture = null
	if sprite_green:
		sprite_green.visible = true
		sprite_green.texture = atlas_tex
		sprite_green.hframes = video_hframes
		sprite_green.vframes = video_vframes
		sprite_green.frame = 0
		sprite_green.centered = false
		sprite_green.offset = -root_anchor
		sprite_green.position = Vector2.ZERO
		if sprite_green.material:
			var gmat = sprite_green.material as ShaderMaterial
			gmat.set_shader_parameter("root_uv_y", root_uv_y)
			gmat.set_shader_parameter("crossfade_alpha", 1.0)
			gmat.set_shader_parameter("tint_color", Color.WHITE)
			gmat.set_shader_parameter("uv_y_range", init_uv_range)

	set_xray_alpha(current_xray_alpha)

	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		if not shadow:
			continue
		shadow.texture = atlas_tex
		shadow.hframes = video_hframes
		shadow.vframes = video_vframes
		shadow.frame = 0
		shadow.centered = false
		shadow.offset = -root_anchor
		if shadow.material:
			var smat = shadow.material as ShaderMaterial
			smat.set_shader_parameter("tRed", null)
			smat.set_shader_parameter("tGreen", atlas_tex)
			smat.set_shader_parameter("tBlue", null)
			smat.set_shader_parameter("root_uv_y", root_uv_y)
			smat.set_shader_parameter("uv_y_range", init_uv_range)

	_apply_effective_scale()

func update_turntable_orbit(orbit_deg: float, delta: float = 0.016, force_instant: bool = false):
	if is_video_atlas:
		if not video_atlas_tex or video_frame_count <= 0:
			return
		var eff_deg_v: float = fposmod(orbit_deg + base_yaw_deg, 360.0)
		var step_v: float = 360.0 / float(video_frame_count)
		var f_idx: int = int(round(eff_deg_v / step_v)) % video_frame_count
		current_video_frame = f_idx
		current_sector_deg = int(round(float(f_idx) * step_v)) % 360
		var row_idx: int = int(floor(float(f_idx) / float(max(video_hframes, 1))))
		var row_h: float = 1.0 / float(max(video_vframes, 1))
		var uv_range := Vector2(float(row_idx) * row_h, float(row_idx + 1) * row_h)
		if sprite_green:
			sprite_green.frame = f_idx
			if sprite_green.material:
				(sprite_green.material as ShaderMaterial).set_shader_parameter("uv_y_range", uv_range)
		for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
			if shadow:
				shadow.frame = f_idx
				if shadow.material:
					(shadow.material as ShaderMaterial).set_shader_parameter("uv_y_range", uv_range)
		_apply_effective_scale()
		if caption_prefix != "":
			set_lab_caption("%s [Video 36F: %03d deg]" % [caption_prefix, current_sector_deg])
		return

	if not is_turntable or turntable_frames.is_empty():
		return

	var eff_deg: float = fposmod(orbit_deg + base_yaw_deg, 360.0)
	var step_int: int = 45 if turntable_sector_count >= 8 else 90
	var step_deg: float = float(step_int)
	var num_sectors: int = int(360 / step_int)
	var sector_idx: int = int(round(eff_deg / step_deg)) % num_sectors
	var target_sector: int = (sector_idx * step_int) % 360

	var chosen_sector: int = target_sector
	var curr_valid_for_mode: bool = (current_sector_deg % step_int) == 0 and turntable_frames.has(current_sector_deg)
	if not force_instant and curr_valid_for_mode and target_sector != current_sector_deg:
		var dist_from_curr: float = abs(fposmod(eff_deg - float(current_sector_deg) + 180.0, 360.0) - 180.0)
		if dist_from_curr <= (step_deg * 0.5 + 1.5):
			chosen_sector = current_sector_deg

	if not turntable_frames.has(chosen_sector):
		var best_ang: int = current_sector_deg
		var best_dist: float = 9999.0
		for k in turntable_frames.keys():
			var k_int: int = int(k)
			if (k_int % step_int) != 0 and turntable_frames.has(0):
				continue
			var d: float = abs(fposmod(eff_deg - float(k_int) + 180.0, 360.0) - 180.0)
			if d < best_dist:
				best_dist = d
				best_ang = k_int
		if best_dist > 9000.0:
			for k in turntable_frames.keys():
				var k_int: int = int(k)
				var d: float = abs(fposmod(eff_deg - float(k_int) + 180.0, 360.0) - 180.0)
				if d < best_dist:
					best_dist = d
					best_ang = k_int
		chosen_sector = best_ang

	var sector_changed: bool = (chosen_sector != current_sector_deg)
	if sector_changed:
		prev_sector_deg = current_sector_deg
		current_sector_deg = chosen_sector
		if turntable_frames.has(current_sector_deg):
			_apply_turntable_frame(turntable_frames[current_sector_deg])
		if force_instant or turntable_transition_mode == TRANSITION_SNAP:
			transition_progress = 1.0
		else:
			transition_progress = min(1.0, max(0.0, delta) / 0.13)
	else:
		if force_instant or turntable_transition_mode == TRANSITION_SNAP:
			transition_progress = 1.0
		else:
			transition_progress = min(1.0, transition_progress + max(0.0, delta) / 0.13)

	var delta_from_sector: float = fposmod(eff_deg - float(current_sector_deg) + 180.0, 360.0) - 180.0
	sub_sector_norm = clampf(delta_from_sector / (step_deg * 0.5), -1.0, 1.0)

	_apply_effective_scale()
	if caption_prefix != "":
		set_lab_caption("%s [%d deg]" % [caption_prefix, current_sector_deg])

func load_plant(id: String):
	plant_id = id
	var base_path = "res://assets/plants/" + id + "/01/"

	var tex_red = load(base_path + "layer_red.png") if ResourceLoader.exists(base_path + "layer_red.png") else null
	var tex_green = load(base_path + "layer_green.png") if ResourceLoader.exists(base_path + "layer_green.png") else null
	var tex_blue = load(base_path + "layer_blue.png") if ResourceLoader.exists(base_path + "layer_blue.png") else null

	var target_h: float = desired_height_px
	var root_w: float = meta_root_width
	var override_anchor_uv: Vector2 = Vector2(-1.0, -1.0)

	var meta_path = base_path + "metadata.json"
	if FileAccess.file_exists(meta_path):
		var meta_text = FileAccess.get_file_as_string(meta_path)
		var meta = JSON.parse_string(meta_text)
		if meta is Dictionary:
			if root_w <= 0.0 and meta.has("root_width"):
				root_w = float(meta["root_width"])
			if target_h <= 0.0:
				var ai = meta.get("ai_analysis", {})
				if ai is Dictionary and ai.has("suggested_game_height_px"):
					var h_arr = ai["suggested_game_height_px"]
					if h_arr is Array and h_arr.size() >= 2:
						var min_px: float = clampf(float(h_arr[0]), 35.0, 220.0)
						var max_px: float = clampf(float(h_arr[1]), min_px, 220.0)
						target_h = randf_range(min_px, max_px)
			var ra = meta.get("root_anchor", null)
			if ra is Array and ra.size() >= 2:
				if (ra[0] is float or ra[0] is int) and (ra[1] is float or ra[1] is int):
					override_anchor_uv = Vector2(float(ra[0]), float(ra[1]))

	setup_with_textures(id, tex_red, tex_green, tex_blue, target_h, root_w, override_anchor_uv)

func setup_with_textures(id: String, tex_red: Texture2D, tex_green: Texture2D, tex_blue: Texture2D, target_h_px: float = -1.0, root_w_px: float = -1.0, override_anchor_uv: Vector2 = Vector2(-1.0, -1.0)):
	_ensure_node_refs()

	plant_id = id
	desired_height_px = target_h_px
	meta_root_width = root_w_px

	var ref_tex = tex_green if tex_green else (tex_red if tex_red else tex_blue)
	if not ref_tex:
		push_error("Failed to load plant textures for " + id)
		return

	var images: Array = []
	for t in [tex_red, tex_green, tex_blue]:
		if t:
			var img = t.get_image()
			if img:
				if img.is_compressed():
					img.decompress()
				images.append(img)

	if images.is_empty():
		push_error("Failed to extract image data for " + id)
		return

	var width: int = images[0].get_width()
	var height: int = images[0].get_height()

	var top_y: int = height
	var bottom_y: int = -1
	var min_x: int = width
	var max_x: int = 0

	for img in images:
		var used: Rect2i = img.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			top_y = min(top_y, used.position.y)
			min_x = min(min_x, used.position.x)
			max_x = max(max_x, used.position.x + used.size.x - 1)

	if min_x > max_x:
		min_x = 0
		max_x = max(0, width - 1)
	else:
		min_x = clampi(min_x, 0, max(0, width - 1))
		max_x = clampi(max_x, 0, max(0, width - 1))

	for y in range(height - 1, -1, -1):
		for x in range(min_x, max_x + 1):
			for img in images:
				if x < img.get_width() and y < img.get_height() and img.get_pixel(x, y).a > 0.15:
					bottom_y = y
					break
			if bottom_y != -1:
				break
		if bottom_y != -1:
			break

	if bottom_y == -1:
		bottom_y = max(0, height - 1)
	if top_y >= bottom_y:
		top_y = 0

	var sum_x: float = 0.0
	var count: int = 0
	var start_y: int = max(0, bottom_y - 7)
	for y in range(start_y, bottom_y + 1):
		for x in range(min_x, max_x + 1):
			var has_alpha := false
			for img in images:
				if x < img.get_width() and y < img.get_height() and img.get_pixel(x, y).a > 0.15:
					has_alpha = true
					break
			if has_alpha:
				sum_x += float(x)
				count += 1

	var root_x: float = float(width) / 2.0
	if count > 0:
		root_x = sum_x / float(count)

	if override_anchor_uv.x >= 0.0 and override_anchor_uv.y > 0.0:
		root_anchor = Vector2(
			clampf(override_anchor_uv.x, 0.05, 0.95) * float(width),
			clampf(override_anchor_uv.y, 0.60, 1.0) * float(height)
		)
		root_uv_y = clampf(override_anchor_uv.y, 0.05, 1.0)
	else:
		root_anchor = Vector2(root_x, float(bottom_y))
		root_uv_y = float(bottom_y) / float(max(height, 1))

	var raw_w: float = max(24.0, float(max_x - min_x))
	var raw_h: float = max(24.0, float(bottom_y - top_y))

	_cached_raw_w = raw_w
	_cached_raw_h = raw_h
	_cached_base_scale = (target_h_px / raw_h) if target_h_px > 0.0 else 1.0
	_cached_base_rw = root_w_px if root_w_px > 0.0 else max(10.0, raw_w * 0.15)
	_textures_ready = true

	# Configure Foliage Sprites
	var setups = [
		{"sprite": sprite_red, "tex": tex_red},
		{"sprite": sprite_green, "tex": tex_green},
		{"sprite": sprite_blue, "tex": tex_blue}
	]

	for setup in setups:
		var spr = setup.sprite
		var tex = setup.tex
		if not spr:
			continue
		if tex:
			spr.visible = true
			spr.texture = tex
			spr.centered = false
			spr.offset = -root_anchor
			if spr.material:
				var mat = spr.material.duplicate() as ShaderMaterial
				mat.set_shader_parameter("root_uv_y", root_uv_y)
				spr.material = mat
		else:
			spr.visible = false
			spr.texture = null

	set_xray_alpha(current_xray_alpha)

	# Configure shadows
	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		if not shadow:
			continue
		shadow.texture = ref_tex
		shadow.centered = false
		shadow.offset = -root_anchor
		if shadow.material:
			var smat = shadow.material.duplicate() as ShaderMaterial
			smat.set_shader_parameter("tRed", tex_red)
			smat.set_shader_parameter("tGreen", tex_green)
			smat.set_shader_parameter("tBlue", tex_blue)
			smat.set_shader_parameter("root_uv_y", root_uv_y)
			shadow.material = smat

	_apply_effective_scale()
