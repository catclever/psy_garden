extends Node2D

@onready var y_sort = $YSort
@onready var mouse_lantern = $MouseLantern
@onready var mouse_lantern_light = $MouseLantern/PointLight2D
@onready var incense_burner = $IncenseBurner

var plant_scene = preload("res://Plant.tscn")

var active_plants: Array = []
var available_plants: Array = [
	"plant_0000", "plant_0001", "plant_0010", "plant_0018",
	"plant_0022", "plant_0038", "plant_0067", "plant_0164",
	"plant_0304", "plant_0353"
]

var manifest_records: Array = []
var texture_cache: Dictionary = {}
var web_base_url: String = ""
var footprints_visible: bool = true
var placed_footprints: Array = []
var generation_id: int = 0
var manifest_loaded_once: bool = false
var _initial_garden_auto_seeded: bool = false

const MAX_CONCURRENT_HTTP: int = 8
const LAB_ASSET_VER: String = "047"
var _http_pool: Array = []
var _http_busy: Dictionary = {}
var _http_queue: Array = []
var _in_flight_callbacks: Dictionary = {}

const DEPTH_Y_MIN: float = 140.0
const DEPTH_Y_MAX: float = 840.0
const DEPTH_Y_REF: float = 490.0
const DEPTH_HALF_SPAN: float = 350.0
const BASE_PERSPECTIVE_SLOPE: float = 0.25

const COURTYARD_CENTER: Vector2 = Vector2(720.0, 450.0)
const COURTYARD_HALF_SIZE: float = 420.0
const DEFAULT_PITCH_RATIO: float = 0.76

var sun_angle_deg: float = 45.0
var sun_length: float = 1.0
var lantern_intensity: float = 1.0
var lantern_radius: float = 520.0
var perspective_strength: float = 1.0

var camera_pitch_ratio: float = DEFAULT_PITCH_RATIO
var orbit_angle_deg: float = 0.0
var target_orbit_deg: float = 0.0
var xray_enabled: bool = true
var _right_dragging: bool = false
var _syncing_orbit_ui: bool = false
var _test_mouse_override: Vector2 = Vector2(-99999.0, -99999.0)
var ground_canvas: Node2D = null
var pitch_label: Label = null
var pitch_slider: HSlider = null
var orbit_label: Label = null
var orbit_slider: HSlider = null
var btn_toggle_xray: Button = null

var color_red: Color = Color.WHITE
var color_green: Color = Color.WHITE
var color_blue: Color = Color.WHITE

var fleet_status_label: Label = null
var btn_toggle_footprints: Button = null
var perspective_label: Label = null
var perspective_slider: HSlider = null

var turntable_lab_active: bool = false
var turntable_sector_count: int = 8
var turntable_transition_mode: int = 1
var turntable_index_data: Dictionary = {}
var _turntable_index_loading: bool = false
var _pending_url_lab: bool = false
var btn_turntable_lab: Button = null
var btn_turntable_dirs: Button = null
var btn_turntable_transition: Button = null

var video_lab_active: bool = false
var video_atlas_meta: Dictionary = {}
var _video_meta_loading: bool = false
var _pending_url_video_lab: bool = false
var btn_video_lab: Button = null

var video_gallery_active: bool = false
var video_gallery_index: Dictionary = {}
var _video_gallery_loading: bool = false
var _pending_url_video_gallery: bool = false
var btn_video_gallery: Button = null

var video36_by_id: Dictionary = {}
var _manifest_by_id: Dictionary = {}
var _next_plant_uid: int = 1
var selected_plant = null
var planting_species_id: String = ""
var _planting_preview_pos: Vector2 = Vector2.ZERO
var _planting_preview_valid: bool = false
var _planting_preview_fp_w: float = 24.0
var _planting_preview_fp_h: float = 14.0
var _left_press_active: bool = false
var _left_press_start_ms: int = 0
var _left_press_pos: Vector2 = Vector2.ZERO
var _left_long_press_fired: bool = false
var _js_bridge_callback = null

func _ready():
	mouse_lantern.global_position = Vector2(680, 420)
	ground_canvas = Node2D.new()
	ground_canvas.name = "ZenGround"
	ground_canvas.z_index = -80
	ground_canvas.z_as_relative = false
	add_child(ground_canvas)
	ground_canvas.draw.connect(_draw_zen_ground)
	_ready_ui()
	_init_http_pool()
	_init_js_bridge()
	if OS.has_feature("web"):
		var lab_param = JavaScriptBridge.eval("(function(){try{return new URLSearchParams(window.location.search).get(\"lab\")||new URLSearchParams(window.top.location.search).get(\"lab\")||\"\";}catch(e){return \"\";}})()", true)
		var lab_str: String = str(lab_param).strip_edges().to_lower()
		if lab_str == "3a":
			_pending_url_lab = true
		elif lab_str == "video36":
			_pending_url_video_lab = true
		elif lab_str == "video_gallery":
			_pending_url_video_gallery = true
	if _pending_url_lab:
		call_deferred("enter_turntable_lab")
	elif _pending_url_video_lab:
		call_deferred("enter_video_turntable_lab")
	elif _pending_url_video_gallery:
		call_deferred("enter_video_gallery")
	else:
		generate_demo_layout()
	call_deferred("_load_video36_registry")
	call_deferred("_load_manifest")
	ground_canvas.queue_redraw()

func get_depth_scale(y_pos: float, strength: float = -1.0) -> float:
	var eff_strength: float = perspective_strength if strength < 0.0 else clampf(strength, 0.0, 1.5)
	var norm_y: float = clampf((y_pos - DEPTH_Y_REF) / DEPTH_HALF_SPAN, -1.35, 1.35)
	return clampf(1.0 + norm_y * BASE_PERSPECTIVE_SLOPE * eff_strength, 0.45, 1.85)

func project_courtyard_point(courtyard_pt: Vector2, orbit_deg: float = NAN, pitch: float = -1.0, p_strength: float = -1.0) -> Dictionary:
	var eff_deg: float = orbit_angle_deg if is_nan(orbit_deg) else orbit_deg
	var eff_pitch: float = camera_pitch_ratio if pitch < 0.0 else clampf(pitch, 0.45, 0.92)
	var eff_persp: float = perspective_strength if p_strength < 0.0 else clampf(p_strength, 0.0, 1.5)
	var rad: float = deg_to_rad(eff_deg)
	var c: float = cos(rad)
	var sn: float = sin(rad)
	var rx: float = courtyard_pt.x * c - courtyard_pt.y * sn
	var ry: float = courtyard_pt.x * sn + courtyard_pt.y * c
	var m: float = (BASE_PERSPECTIVE_SLOPE * eff_persp) / DEPTH_HALF_SPAN
	var p0: float = 1.0 + m * (COURTYARD_CENTER.y - DEPTH_Y_REF)
	var denom: float = max(0.32, 1.0 - m * eff_pitch * ry)
	var p_proj: float = clampf(p0 / denom, 0.45, 1.85)
	var sy: float = COURTYARD_CENTER.y + ry * eff_pitch * p_proj
	var sx: float = COURTYARD_CENTER.x + rx * p_proj
	var d_scale: float = get_depth_scale(sy, eff_persp)
	return {"screen_pos": Vector2(sx, sy), "depth_scale": d_scale, "rx": rx, "ry": ry}

func screen_to_courtyard(screen_pos: Vector2) -> Vector2:
	var d_scale: float = max(get_depth_scale(screen_pos.y), 0.25)
	var eff_pitch: float = max(camera_pitch_ratio, 0.25)
	var rx: float = (screen_pos.x - COURTYARD_CENTER.x) / d_scale
	var ry: float = (screen_pos.y - COURTYARD_CENTER.y) / (eff_pitch * d_scale)
	var rad: float = -deg_to_rad(orbit_angle_deg)
	var c: float = cos(rad)
	var sn: float = sin(rad)
	return Vector2(rx * c - ry * sn, rx * sn + ry * c)

func _wrap_deg_180(deg: float) -> float:
	return fposmod(deg + 180.0, 360.0) - 180.0

func _set_orbit_target(deg: float, snap_immediate: bool = false):
	target_orbit_deg = _wrap_deg_180(deg)
	if snap_immediate:
		orbit_angle_deg = target_orbit_deg
		_refresh_courtyard_projection()
	if orbit_label:
		orbit_label.text = "Courtyard Angle: %d deg [Q/E]" % int(round(orbit_angle_deg))
	if orbit_slider and not _syncing_orbit_ui:
		_syncing_orbit_ui = true
		orbit_slider.value = orbit_angle_deg
		_syncing_orbit_ui = false

func _refresh_courtyard_projection():
	for p in active_plants:
		if is_instance_valid(p):
			if not p.has_courtyard_pos:
				p.courtyard_pos = screen_to_courtyard(p.global_position)
				p.has_courtyard_pos = true
			var proj: Dictionary = project_courtyard_point(p.courtyard_pos)
			p.global_position = proj["screen_pos"]
			if p.has_method("set_ground_pitch"):
				p.set_ground_pitch(camera_pitch_ratio)
			if p.has_method("set_perspective_scale"):
				p.set_perspective_scale(proj["depth_scale"])
			if p.is_turntable and p.has_method("update_turntable_orbit"):
				p.update_turntable_orbit(orbit_angle_deg, 0.0, false)
	if turntable_lab_active or video_lab_active or video_gallery_active:
		_update_lab_status_label()
	if ground_canvas:
		ground_canvas.queue_redraw()

func _refresh_all_plants_perspective():
	_refresh_courtyard_projection()

func _draw_zen_ground():
	if not ground_canvas:
		return
	var bg_col: Color = bg.color if is_instance_valid(bg) else Color("#9e8f63")
	var lum: float = bg_col.get_luminance()
	var is_dark: bool = lum < 0.25
	var ring_col: Color = Color(0.78, 0.84, 0.90, 0.14) if is_dark else Color(0.24, 0.20, 0.14, 0.15)
	var grid_col: Color = Color(0.72, 0.78, 0.85, 0.09) if is_dark else Color(0.28, 0.23, 0.16, 0.10)
	var border_col: Color = Color(0.76, 0.80, 0.86, 0.22) if is_dark else Color(0.24, 0.19, 0.13, 0.26)
	var axis_col: Color = Color(0.82, 0.86, 0.92, 0.16) if is_dark else Color(0.25, 0.21, 0.14, 0.18)

	# Karesansui raked-sand concentric rings strictly inside courtyard square
	var ring_radii: Array = [85.0, 140.0, 195.0, 255.0, 315.0, 375.0]
	var segs: int = 48
	for r in ring_radii:
		var pts := PackedVector2Array()
		for i in range(segs + 1):
			var ang: float = (float(i) / float(segs)) * TAU
			var pt: Vector2 = Vector2(cos(ang) * float(r), sin(ang) * float(r))
			pts.append(project_courtyard_point(pt)["screen_pos"])
		ground_canvas.draw_polyline(pts, ring_col, 1.25, true)

	# Symmetric 6x6 perspective courtyard inner grid lines step 140
	var half_size: float = COURTYARD_HALF_SIZE
	var grid_segs: int = 16
	for gx in [-280.0, -140.0, 0.0, 140.0, 280.0]:
		var pts := PackedVector2Array()
		for i in range(grid_segs + 1):
			var t: float = float(i) / float(grid_segs)
			var gy: float = lerpf(-half_size, half_size, t)
			pts.append(project_courtyard_point(Vector2(gx, gy))["screen_pos"])
		ground_canvas.draw_polyline(pts, grid_col, 1.0, true)

	for gy in [-280.0, -140.0, 0.0, 140.0, 280.0]:
		var pts := PackedVector2Array()
		for i in range(grid_segs + 1):
			var t: float = float(i) / float(grid_segs)
			var gx: float = lerpf(-half_size, half_size, t)
			pts.append(project_courtyard_point(Vector2(gx, gy))["screen_pos"])
		ground_canvas.draw_polyline(pts, grid_col, 1.0, true)

	# Perspective courtyard timber/stone double border (symmetric world square)
	for b_half in [half_size, half_size - 12.0]:
		var border_corners: Array = [
			Vector2(-b_half, -b_half),
			Vector2(b_half, -b_half),
			Vector2(b_half, b_half),
			Vector2(-b_half, b_half)
		]
		var border_pts := PackedVector2Array()
		for c_idx in range(4):
			var p0: Vector2 = border_corners[c_idx]
			var p1: Vector2 = border_corners[(c_idx + 1) % 4]
			for i in range(grid_segs):
				var t: float = float(i) / float(grid_segs)
				border_pts.append(project_courtyard_point(p0.lerp(p1, t))["screen_pos"])
		border_pts.append(project_courtyard_point(border_corners[0])["screen_pos"])
		var b_width: float = 2.0 if abs(b_half - half_size) < 1.0 else 1.0
		var b_color: Color = border_col if abs(b_half - half_size) < 1.0 else grid_col
		ground_canvas.draw_polyline(border_pts, b_color, b_width, true)

	# Symmetric cardinal markers along X/Y axes
	var marker_dist: float = 390.0
	var marker_len: float = 20.0
	var cardinal_segs: Array = [
		[Vector2(marker_dist - marker_len, 0.0), Vector2(marker_dist + marker_len, 0.0)],
		[Vector2(-marker_dist - marker_len, 0.0), Vector2(-marker_dist + marker_len, 0.0)],
		[Vector2(0.0, marker_dist - marker_len), Vector2(0.0, marker_dist + marker_len)],
		[Vector2(0.0, -marker_dist - marker_len), Vector2(0.0, -marker_dist + marker_len)]
	]
	for seg in cardinal_segs:
		var a: Vector2 = project_courtyard_point(seg[0])["screen_pos"]
		var b: Vector2 = project_courtyard_point(seg[1])["screen_pos"]
		ground_canvas.draw_line(a, b, axis_col, 2.0, true)
		ground_canvas.draw_circle(b, 3.0, axis_col)

	if planting_species_id != "" and _planting_preview_pos.length_squared() > 1.0:
		var prev_col: Color = Color(0.35, 0.95, 0.55, 0.85) if _planting_preview_valid else Color(0.95, 0.35, 0.32, 0.88)
		var fill_col := Color(prev_col.r, prev_col.g, prev_col.b, 0.16)
		ground_canvas.draw_set_transform(_planting_preview_pos, 0.0, Vector2(1.0, camera_pitch_ratio))
		ground_canvas.draw_circle(Vector2.ZERO, max(8.0, _planting_preview_fp_w * 0.5), fill_col)
		ground_canvas.draw_arc(Vector2.ZERO, max(8.0, _planting_preview_fp_w * 0.5), 0.0, TAU, 36, prev_col, 2.0, true)
		ground_canvas.draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)

func _unhandled_input(event):
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		if planting_species_id != "":
			set_planting_mode("")
	elif event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT:
			_right_dragging = event.pressed
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_left_press_active = true
				_left_press_start_ms = Time.get_ticks_msec()
				_left_press_pos = event.position
				_left_long_press_fired = false
			elif _left_press_active:
				_left_press_active = false
				if not _left_long_press_fired and event.position.distance_to(_left_press_pos) <= 12.0:
					if planting_species_id != "":
						var res: Dictionary = plant_species_at_screen_pos(planting_species_id, event.position)
						_emit_js_event("hand_plant_result", res)
					else:
						var held_ms: int = Time.get_ticks_msec() - _left_press_start_ms
						var hit = pick_plant_at_screen_pos(event.position)
						_select_plant(hit, held_ms >= 350)
	elif event is InputEventMouseMotion:
		if _right_dragging:
			target_orbit_deg = _wrap_deg_180(target_orbit_deg + event.relative.x * 0.35)
		elif planting_species_id != "":
			_planting_preview_pos = event.position
			_update_planting_preview_validity()
			if ground_canvas:
				ground_canvas.queue_redraw()

func compute_lantern_xray_alpha(p, light_pos: Vector2) -> float:
	if not xray_enabled or not is_instance_valid(p):
		return 1.0
	if p.visual_height <= 48.0:
		return 1.0
	var dy_front: float = p.global_position.y - light_pos.y
	if dy_front <= 6.0:
		return 1.0
	var front_factor: float = smoothstep(6.0, 26.0, dy_front)
	var canopy_top_y: float = p.global_position.y - p.visual_height * 0.96
	var top_clearance: float = (light_pos.y + 22.0) - canopy_top_y
	var canopy_factor: float = smoothstep(0.0, 36.0, top_clearance)
	var horiz_reach: float = max(p.visual_width * 0.46, 36.0) + 28.0
	var dx: float = abs(p.global_position.x - light_pos.x)
	var horiz_factor: float = 1.0 - smoothstep(horiz_reach * 0.55, horiz_reach, dx)
	var occ: float = front_factor * canopy_factor * horiz_factor
	return lerpf(1.0, 0.32, occ)

func _process(delta):
	var key_dir: float = 0.0
	if Input.is_physical_key_pressed(KEY_Q):
		key_dir -= 1.0
	if Input.is_physical_key_pressed(KEY_E):
		key_dir += 1.0
	if key_dir != 0.0:
		target_orbit_deg = _wrap_deg_180(target_orbit_deg + key_dir * 75.0 * delta)

	var orbit_diff: float = _wrap_deg_180(target_orbit_deg - orbit_angle_deg)
	if abs(orbit_diff) > 0.01:
		var step_weight: float = min(1.0, 10.0 * delta)
		orbit_angle_deg = _wrap_deg_180(orbit_angle_deg + orbit_diff * step_weight)
		if abs(_wrap_deg_180(target_orbit_deg - orbit_angle_deg)) <= 0.02:
			orbit_angle_deg = _wrap_deg_180(target_orbit_deg)
		if orbit_label:
			orbit_label.text = "Courtyard Angle: %d deg [Q/E]" % int(round(orbit_angle_deg))
		if orbit_slider:
			_syncing_orbit_ui = true
			orbit_slider.value = orbit_angle_deg
			_syncing_orbit_ui = false
		_refresh_courtyard_projection()

	for p in active_plants:
		if is_instance_valid(p) and p.is_turntable and p.has_method("update_turntable_orbit"):
			p.update_turntable_orbit(orbit_angle_deg, delta, false)

	if _left_press_active and not _left_long_press_fired and planting_species_id == "":
		if (Time.get_ticks_msec() - _left_press_start_ms) >= 420:
			var cur_mouse: Vector2 = get_global_mouse_position()
			if cur_mouse.distance_to(_left_press_pos) <= 12.0:
				_left_long_press_fired = true
				var lp_hit = pick_plant_at_screen_pos(_left_press_pos)
				_select_plant(lp_hit, true)

	var target_mouse: Vector2
	if _test_mouse_override.x > -90000.0:
		target_mouse = _test_mouse_override
	else:
		var raw_mouse = get_global_mouse_position()
		target_mouse = raw_mouse if raw_mouse.length_squared() > 4.0 else Vector2(680, 420)
	mouse_lantern.global_position = mouse_lantern.global_position.lerp(target_mouse, min(1.0, 25.0 * delta))
	var mouse_pos = mouse_lantern.global_position
	mouse_lantern_light.energy = lantern_intensity * 0.85

	var sun_rad = deg_to_rad(sun_angle_deg + orbit_angle_deg)
	var sun_dir = Vector2(cos(sun_rad), sin(sun_rad))
	var shadow_scale_y: float = camera_pitch_ratio

	# 1. Update plant shadows via synthesized single vector
	var plant_shadow_info: Array = []

	for p in active_plants:
		if not is_instance_valid(p): continue

		# Sun shadow vector (in isometric unscaled space)
		var v_sun = Vector2.ZERO
		if sun_length > 0.001:
			v_sun = sun_dir * sun_length

		# Lantern shadow vector
		var iso_delta = (p.global_position - mouse_pos) / Vector2(1.0, shadow_scale_y)
		var dist = iso_delta.length()
		var v_lantern = Vector2.ZERO
		if dist < lantern_radius and dist > 5.0 and lantern_intensity > 0.001:
			var proximity = clamp(1.0 - dist / lantern_radius, 0.0, 1.0)
			var lantern_weight = pow(proximity, 1.25) * 1.85 * lantern_intensity
			v_lantern = iso_delta.normalized() * lantern_weight

		# Incense shadow vector
		var inc_iso = (p.global_position - incense_burner.global_position) / Vector2(1.0, shadow_scale_y)
		var inc_dist = inc_iso.length()
		var v_incense = Vector2.ZERO
		if inc_dist < 320.0 and inc_dist > 5.0:
			var inc_proximity = clamp(1.0 - inc_dist / 320.0, 0.0, 1.0)
			v_incense = inc_iso.normalized() * (pow(inc_proximity, 1.25) * 0.45)

		# Combined synthesized vector (single unified shadow)
		var v_combined = v_sun + v_lantern + v_incense
		var combined_len = v_combined.length()

		if combined_len > 0.01:
			var combined_angle = v_combined.angle() + PI / 2.0
			var shadow_len_clamped = clamp(combined_len, 0.15, 2.2)
			p.sun_shadow_pivot.rotation = combined_angle
			p.sun_shadow_pivot.scale = Vector2(1.0, shadow_len_clamped)

			var base_alpha = clamp(0.72 * min(combined_len, 1.0), 0.0, 0.82)
			if p.sun_shadow and p.sun_shadow.material:
				p.sun_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, base_alpha))

			# Calculate shadow world vector for inter-plant occlusion corridor
			var world_dir = Vector2(cos(v_combined.angle()), sin(v_combined.angle()) * shadow_scale_y)
			var world_len = p.visual_height * shadow_len_clamped * world_dir.length()
			plant_shadow_info.append({"plant": p, "dir": world_dir.normalized(), "len": world_len})
		else:
			if p.sun_shadow and p.sun_shadow.material:
				p.sun_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, 0))

		# Ensure secondary shadow pivots are hidden to maintain exactly ONE unified shadow
		if p.lantern_shadow_pivot.visible:
			p.lantern_shadow_pivot.visible = false
		if p.incense_shadow_pivot.visible:
			p.incense_shadow_pivot.visible = false

	# 2. Inter-plant shadow occlusion using synthesized shadow corridors
	for b in active_plants:
		if not is_instance_valid(b): continue
		var cover_height: float = 0.0
		var cover_strength: float = 0.0

		for caster_info in plant_shadow_info:
			var a = caster_info["plant"]
			if a == b or not is_instance_valid(a): continue

			var unit_dir = caster_info["dir"]
			var shadow_world_len = caster_info["len"]

			if shadow_world_len > 8.0:
				var to_b = b.global_position - a.global_position
				var along = to_b.dot(unit_dir)
				var perp = abs(to_b.dot(Vector2(-unit_dir.y, unit_dir.x)))
				var half_w = a.visual_width * 0.32

				if along > 8.0 and along < shadow_world_len and perp < half_w:
					var reach_ratio = clamp((1.0 - along / shadow_world_len) * (a.visual_height / max(b.visual_height, 1.0)), 0.0, 1.0)
					var lateral_fit = smoothstep(half_w, half_w * 0.25, perp)
					var strength = lateral_fit * 0.65

					cover_height = max(cover_height, reach_ratio)
					cover_strength = clamp(cover_strength + strength, 0.0, 0.70)

		# Apply to B's shaders (all 3 sprites + ghost sprites if turntable)
		for sprite in [b.sprite_red, b.sprite_green, b.sprite_blue, b._prev_red, b._prev_green, b._prev_blue]:
			if sprite and sprite.visible and sprite.material:
				var mat = sprite.material as ShaderMaterial
				mat.set_shader_parameter("occlusion_height", cover_height)
				mat.set_shader_parameter("occlusion_darken", cover_strength)

	# 3. Directional lantern-only X-Ray pass: fade only plants strictly between camera and lantern
	var xray_weight: float = min(1.0, 10.0 * delta)
	for f in active_plants:
		if not is_instance_valid(f):
			continue
		f.target_xray_alpha = compute_lantern_xray_alpha(f, mouse_pos)
		f.current_xray_alpha = lerpf(f.current_xray_alpha, f.target_xray_alpha, xray_weight)
		if f.has_method("set_xray_alpha"):
			f.set_xray_alpha(f.current_xray_alpha)

func clear_garden():
	generation_id += 1
	_initial_garden_auto_seeded = false
	selected_plant = null
	for p in active_plants:
		if is_instance_valid(p):
			p.queue_free()
	active_plants.clear()
	placed_footprints.clear()
	if ground_canvas:
		ground_canvas.queue_redraw()

func add_plant(id: String, pos: Vector2):
	var p = plant_scene.instantiate()
	p.plant_id = id
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	p.ground_pitch_ratio = camera_pitch_ratio
	p.courtyard_pos = screen_to_courtyard(pos)
	p.has_courtyard_pos = true
	p.perspective_scale = get_depth_scale(pos.y)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	active_plants.append(p)
	placed_footprints.append({"pos": pos, "fp_w": p.fp_w, "fp_h": p.fp_h})

	update_plant_colors(p)
	return p

func update_plant_colors(p=null):
	var targets = [p] if p else active_plants
	for t in targets:
		if not is_instance_valid(t): continue
		if t.is_video_atlas:
			if t.sprite_green and t.sprite_green.material:
				(t.sprite_green.material as ShaderMaterial).set_shader_parameter("tint_color", t.custom_tint_color)
			continue
		for spr in [t.sprite_red, t._prev_red]:
			if spr and spr.material:
				(spr.material as ShaderMaterial).set_shader_parameter("tint_color", color_red * t.custom_tint_color)
		for spr in [t.sprite_green, t._prev_green]:
			if spr and spr.material:
				(spr.material as ShaderMaterial).set_shader_parameter("tint_color", color_green * t.custom_tint_color)
		for spr in [t.sprite_blue, t._prev_blue]:
			if spr and spr.material:
				(spr.material as ShaderMaterial).set_shader_parameter("tint_color", color_blue * t.custom_tint_color)

func can_place_plant(pos: Vector2, fp_w: float, fp_h: float) -> bool:
	# Avoid HUD top-left panel
	if pos.x < 310.0 and pos.y < 790.0:
		return false
	# Avoid IncenseBurner within 65px of (720, 450)
	var incense_pos: Vector2 = incense_burner.global_position if is_instance_valid(incense_burner) else Vector2(720.0, 450.0)
	if pos.distance_to(incense_pos) < 65.0:
		return false
	# 2.5D elliptical ground collision (canopies in the air may overhang freely)
	for other in placed_footprints:
		var other_pos: Vector2 = other["pos"]
		var dx: float = pos.x - other_pos.x
		var dy: float = pos.y - other_pos.y
		var a: float = (fp_w * 1.15 + float(other["fp_w"]) * 1.15) * 0.5 + 14.0
		var b: float = (fp_h * 1.15 + float(other["fp_h"]) * 1.15) * 0.5 + 9.0
		if ((dx / a) * (dx / a) + (dy / b) * (dy / b)) < 1.0:
			return false
	return true

func _sample_record_metrics(rec: Dictionary) -> Dictionary:
	var min_px: float = 70.0
	var max_px: float = 130.0
	var ai = rec.get("ai_analysis", {})
	if ai is Dictionary and ai.has("suggested_game_height_px"):
		var h_arr = ai["suggested_game_height_px"]
		if h_arr is Array and h_arr.size() >= 2:
			min_px = clampf(float(h_arr[0]), 35.0, 220.0)
			max_px = clampf(float(h_arr[1]), min_px, 220.0)
	var target_h: float = randf_range(min_px, max_px)
	var raw_h: float = max(float(rec.get("height", 250)), 1.0)
	var scale_factor: float = target_h / raw_h
	var root_w: float = float(rec.get("root_width", 24))
	var fp_w: float = max(10.0, root_w * scale_factor)
	var fp_h: float = max(6.0, fp_w * camera_pitch_ratio)
	return {
		"target_h": target_h,
		"scale_factor": scale_factor,
		"root_w": root_w,
		"fp_w": fp_w,
		"fp_h": fp_h
	}

func generate_random_garden(count: int = 36):
	clear_garden()
	var scatter_limit: float = COURTYARD_HALF_SIZE - 35.0
	if manifest_records.is_empty():
		var fallback_count: int = min(count, 16)
		for i in range(fallback_count):
			var id = available_plants[randi() % available_plants.size()]
			for _attempt in range(120):
				var w_pt = Vector2(randf_range(-scatter_limit, scatter_limit), randf_range(-scatter_limit, scatter_limit))
				var proj: Dictionary = project_courtyard_point(w_pt)
				var pos: Vector2 = proj["screen_pos"]
				if pos.x < 110.0 or pos.x > 1350.0 or pos.y < 130.0 or pos.y > 855.0:
					continue
				var depth_s: float = proj["depth_scale"]
				var eff_fp_w: float = max(10.0, 24.0 * depth_s)
				var eff_fp_h: float = max(6.0, 24.0 * depth_s * camera_pitch_ratio)
				if can_place_plant(pos, eff_fp_w, eff_fp_h):
					add_plant(id, pos)
					break
		if ground_canvas:
			ground_canvas.queue_redraw()
		return

	var buckets: Dictionary = {
		"tree": [],
		"shrub": [],
		"flower": [],
		"groundcover": []
	}
	for rec in manifest_records:
		var ai = rec.get("ai_analysis", {})
		var ptype: String = str(ai.get("plant_type", "shrub")) if ai is Dictionary else "shrub"
		if not buckets.has(ptype):
			ptype = "shrub"
		buckets[ptype].append(rec)

	var bucket_order: Array = []
	for key in ["tree", "shrub", "flower", "groundcover"]:
		if not buckets[key].is_empty():
			bucket_order.append(key)
	if bucket_order.is_empty():
		if ground_canvas:
			ground_canvas.queue_redraw()
		return

	var seeded_recs: Array = []
	if count >= 12 and not video36_by_id.is_empty():
		var v_ids: Array = video36_by_id.keys()
		v_ids.sort()
		for vid in v_ids:
			if seeded_recs.size() >= 6:
				break
			if _manifest_by_id.has(vid):
				seeded_recs.append(_manifest_by_id[vid])

	for i in range(count):
		var rec: Dictionary
		if i < seeded_recs.size():
			rec = seeded_recs[i]
		else:
			var adj_i: int = i - seeded_recs.size()
			var b_key: String = bucket_order[adj_i % bucket_order.size()]
			var pool: Array = buckets[b_key]
			rec = pool[randi() % pool.size()]
		var metrics: Dictionary = _sample_record_metrics(rec)
		var fp_w: float = metrics["fp_w"]
		var target_h: float = metrics["target_h"]
		var root_w: float = metrics["root_w"]

		for _attempt in range(120):
			var w_pt = Vector2(randf_range(-scatter_limit, scatter_limit), randf_range(-scatter_limit, scatter_limit))
			var proj: Dictionary = project_courtyard_point(w_pt)
			var pos: Vector2 = proj["screen_pos"]
			if pos.x < 110.0 or pos.x > 1350.0 or pos.y < 130.0 or pos.y > 855.0:
				continue
			var depth_s: float = proj["depth_scale"]
			var eff_fp_w: float = max(10.0, fp_w * depth_s)
			var eff_fp_h: float = max(6.0, eff_fp_w * camera_pitch_ratio)
			if can_place_plant(pos, eff_fp_w, eff_fp_h):
				spawn_manifest_plant(rec, pos, target_h, root_w, eff_fp_w, eff_fp_h, depth_s)
				placed_footprints.append({"pos": pos, "fp_w": eff_fp_w, "fp_h": eff_fp_h})
				break
	if ground_canvas:
		ground_canvas.queue_redraw()

func spawn_manifest_plant(rec: Dictionary, pos: Vector2, target_h: float, root_w: float, fp_w: float, fp_h: float, depth_scale: float = 1.0, extra: Dictionary = {}):
	var p = plant_scene.instantiate()
	var rec_id: String = str(rec.get("name", rec.get("id", "unknown")))
	p.plant_id = rec_id
	p.plant_uid = str(extra.get("uid", "inst_%d" % _next_plant_uid))
	_next_plant_uid += 1
	p.golden_sentence = str(extra.get("golden_sentence", ""))
	if extra.has("tint_hex") and str(extra.get("tint_hex", "")) != "":
		p.custom_tint_color = Color.from_string(str(extra["tint_hex"]), Color.WHITE)
	p.skip_auto_load = true
	p.desired_height_px = target_h
	p.meta_root_width = root_w
	p.fp_w = fp_w
	p.fp_h = fp_h
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	p.ground_pitch_ratio = camera_pitch_ratio
	p.courtyard_pos = screen_to_courtyard(pos)
	p.has_courtyard_pos = true
	p.perspective_scale = depth_scale
	var raw_h: float = max(float(rec.get("height", 250.0)), 1.0)
	p.set_meta("gen_id", generation_id)
	p.set_meta("manifest_raw_h", raw_h)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	active_plants.append(p)

	if video36_by_id.has(rec_id):
		_attach_video36_to_plant(p, video36_by_id[rec_id], raw_h, target_h, root_w, extra)
		return p

	var path_prefix: String = str(rec.get("path_prefix", ""))
	var layer_files = rec.get("layer_files", [])
	var slot_files: Dictionary = {}
	if layer_files is Array:
		for lf in layer_files:
			if lf is Dictionary:
				var slot: String = str(lf.get("slot", ""))
				var file_name: String = str(lf.get("file", ""))
				if slot in ["red", "green", "blue"] and file_name != "":
					slot_files[slot] = file_name

	if slot_files.is_empty():
		return p

	var anchor_uv: Vector2 = Vector2(-1.0, -1.0)
	var raw_anchor = rec.get("root_anchor", null)
	if raw_anchor is Array and raw_anchor.size() >= 2:
		if (raw_anchor[0] is float or raw_anchor[0] is int) and (raw_anchor[1] is float or raw_anchor[1] is int):
			anchor_uv = Vector2(float(raw_anchor[0]), float(raw_anchor[1]))

	var my_gen_id: int = generation_id
	var loaded_textures: Dictionary = {"red": null, "green": null, "blue": null}
	var remaining: Array = [slot_files.size()]

	for slot in slot_files.keys():
		var file_name: String = slot_files[slot]
		_load_layer_texture(path_prefix, file_name, func(tex: Texture2D):
			loaded_textures[slot] = tex
			remaining[0] -= 1
			if remaining[0] == 0:
				if is_instance_valid(p) and my_gen_id == generation_id and not p.is_video_atlas:
					if loaded_textures["red"] or loaded_textures["green"] or loaded_textures["blue"]:
						p.setup_with_textures(
							rec_id,
							loaded_textures["red"],
							loaded_textures["green"],
							loaded_textures["blue"],
							target_h,
							root_w,
							anchor_uv
						)
						p.set_ground_pitch(camera_pitch_ratio)
						p.show_footprint_ring = footprints_visible
						update_plant_colors(p)
		)

	return p

func _attach_video36_to_plant(p, vmeta: Dictionary, raw_h: float, target_h: float, root_w: float, extra: Dictionary = {}):
	if not is_instance_valid(p):
		return
	p.is_turntable = true
	p.is_video_atlas = true
	if extra.has("base_yaw_deg"):
		p.base_yaw_deg = float(extra["base_yaw_deg"])
	else:
		p.base_yaw_deg = randf_range(0.0, 360.0)
	p.caption_prefix = str(extra.get("caption_prefix", ""))

	var path_prefix: String = str(vmeta.get("path_prefix", "assets/turntable_lab/%s_video36" % p.plant_id))
	var atlas_file: String = str(vmeta.get("atlas_file", "atlas_36.webp"))
	var hf: int = int(vmeta.get("hframes", 6))
	var vf: int = int(vmeta.get("vframes", 6))
	var fc: int = int(vmeta.get("frame_count", 36))
	var atlas_crown_h: float = float(vmeta.get("crown_h", 188.0))
	var raw_anchor = vmeta.get("root_anchor_px", [110.0, 212.0])
	var anchor_px := Vector2(110.0, 212.0)
	if raw_anchor is Array and raw_anchor.size() >= 2:
		anchor_px = Vector2(float(raw_anchor[0]), float(raw_anchor[1]))
	var atlas_root_w: float = root_w * (atlas_crown_h / max(raw_h, 1.0))

	var my_gen_id: int = generation_id
	_load_layer_texture(path_prefix, atlas_file, func(tex: Texture2D):
		if is_instance_valid(p) and my_gen_id == generation_id and tex != null:
			p.setup_video_atlas_turntable(p.plant_id, tex, hf, vf, fc, anchor_px, atlas_crown_h, target_h, atlas_root_w)
			p.update_turntable_orbit(orbit_angle_deg, 0.0, true)
			p.set_ground_pitch(camera_pitch_ratio)
			if p.has_courtyard_pos:
				var cur_proj: Dictionary = project_courtyard_point(p.courtyard_pos)
				p.global_position = cur_proj["screen_pos"]
				p.set_perspective_scale(cur_proj["depth_scale"])
			p.show_footprint_ring = footprints_visible
			update_plant_colors(p)
	)

func _upgrade_active_plants_to_video36():
	if turntable_lab_active or video_lab_active or video_gallery_active:
		return
	if video36_by_id.is_empty():
		return
	var upgraded_any: bool = false
	for p in active_plants:
		if not is_instance_valid(p):
			continue
		if p.is_video_atlas:
			upgraded_any = true
			continue
		if not video36_by_id.has(p.plant_id) or not _manifest_by_id.has(p.plant_id):
			continue
		var raw_h: float = float(p.get_meta("manifest_raw_h")) if p.has_meta("manifest_raw_h") else 250.0
		var target_h: float = p.desired_height_px if p.desired_height_px > 0.0 else 130.0
		var root_w: float = p.meta_root_width if p.meta_root_width > 0.0 else 24.0
		_attach_video36_to_plant(p, video36_by_id[p.plant_id], raw_h, target_h, root_w, {})
		upgraded_any = true
	if not upgraded_any and _initial_garden_auto_seeded and not manifest_records.is_empty():
		_initial_garden_auto_seeded = false
		generate_random_garden(36)
	else:
		_initial_garden_auto_seeded = false

func _load_video36_registry():
	_load_json_asset("assets/turntable_lab/video36_index.json", func(parsed):
		if parsed is Dictionary and parsed.get("species") is Array:
			video_gallery_index = parsed
			video36_by_id.clear()
			for sp in parsed["species"]:
				if sp is Dictionary and sp.has("plant_id"):
					video36_by_id[str(sp["plant_id"])] = sp
			if video_gallery_active:
				_populate_video_gallery()
			else:
				_upgrade_active_plants_to_video36()
			_emit_js_event("video36_ready", {"ids": video36_by_id.keys()})
	)

func _load_manifest():
	if OS.has_feature("web"):
		var evaluated = JavaScriptBridge.eval("new URL('../', window.location.href).href", true)
		web_base_url = str(evaluated) if evaluated != null else ""
		if web_base_url != "" and not web_base_url.ends_with("/"):
			web_base_url += "/"
		var manifest_url: String = web_base_url + "assets/plants/manifest.json"
		var req := HTTPRequest.new()
		add_child(req)
		req.request_completed.connect(func(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray):
			req.queue_free()
			if result == HTTPRequest.RESULT_SUCCESS and response_code == 200:
				_parse_and_activate_manifest(body.get_string_from_utf8())
			elif fleet_status_label:
				fleet_status_label.text = "Fleet: 10 fallback species"
		)
		var err = req.request(manifest_url)
		if err != OK:
			req.queue_free()
			if fleet_status_label:
				fleet_status_label.text = "Fleet: 10 fallback species"
	else:
		var local_candidates = [
			"res://../src/assets/plants/manifest.json",
			ProjectSettings.globalize_path("res://").path_join("../src/assets/plants/manifest.json").simplify_path()
		]
		var loaded := false
		for path in local_candidates:
			if FileAccess.file_exists(path):
				var json_text: String = FileAccess.get_file_as_string(path)
				_parse_and_activate_manifest(json_text)
				loaded = true
				break
		if not loaded and fleet_status_label:
			fleet_status_label.text = "Fleet: 10 fallback species"

func _parse_and_activate_manifest(json_text: String):
	var parsed = JSON.parse_string(json_text)
	if not (parsed is Dictionary) or not (parsed.get("plants") is Array):
		if fleet_status_label and not turntable_lab_active and not video_lab_active and not video_gallery_active:
			fleet_status_label.text = "Fleet: manifest parse error"
		return
	manifest_records.clear()
	_manifest_by_id.clear()
	for rec in parsed["plants"]:
		if rec is Dictionary:
			if rec.get("enabled", true) and rec.get("true_color", true):
				manifest_records.append(rec)
				var pid: String = str(rec.get("name", rec.get("id", "")))
				if pid != "":
					_manifest_by_id[pid] = rec

	if turntable_lab_active or _pending_url_lab or video_lab_active or _pending_url_video_lab or video_gallery_active or _pending_url_video_gallery:
		if not manifest_records.is_empty():
			manifest_loaded_once = true
		return

	if fleet_status_label:
		fleet_status_label.text = "Fleet: %d species loaded" % manifest_records.size()

	if not manifest_loaded_once and not manifest_records.is_empty():
		manifest_loaded_once = true
		var need_post_seed_36f: bool = video36_by_id.is_empty()
		generate_random_garden(36)
		_initial_garden_auto_seeded = need_post_seed_36f

func _load_layer_texture(path_prefix: String, file_name: String, callback: Callable):
	var cache_key: String = path_prefix + "/" + file_name
	if texture_cache.has(cache_key):
		callback.call(texture_cache[cache_key])
		return

	var res_path: String = "res://" + cache_key
	if ResourceLoader.exists(res_path):
		var res_tex = load(res_path)
		if res_tex is Texture2D:
			texture_cache[cache_key] = res_tex
			callback.call(res_tex)
			return

	if not OS.has_feature("web"):
		var local_path: String = ProjectSettings.globalize_path("res://").path_join("../src/" + cache_key).simplify_path()
		if FileAccess.file_exists(local_path):
			var img := Image.new()
			if img.load(local_path) == OK:
				var img_tex := ImageTexture.create_from_image(img)
				texture_cache[cache_key] = img_tex
				callback.call(img_tex)
				return

	if _in_flight_callbacks.has(cache_key):
		_in_flight_callbacks[cache_key].append(callback)
		return

	_in_flight_callbacks[cache_key] = [callback]
	var url: String = web_base_url + cache_key + "?v=" + LAB_ASSET_VER
	_http_queue.append({"url": url, "cache_key": cache_key})
	_pump_http_queue()

func _init_http_pool():
	for i in range(MAX_CONCURRENT_HTTP):
		var req := HTTPRequest.new()
		add_child(req)
		req.request_completed.connect(_on_http_pool_completed.bind(req))
		_http_pool.append(req)

func _pump_http_queue():
	if _http_queue.is_empty():
		return
	for req in _http_pool:
		if _http_queue.is_empty():
			break
		if not _http_busy.has(req):
			var job: Dictionary = _http_queue.pop_front()
			_http_busy[req] = job
			var err = req.request(job["url"])
			if err != OK:
				_http_busy.erase(req)
				_finish_http_job(job["cache_key"], null)

func _on_http_pool_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray, req: HTTPRequest):
	var job: Dictionary = _http_busy.get(req, {})
	_http_busy.erase(req)
	var cache_key: String = str(job.get("cache_key", ""))
	var tex: Texture2D = null
	if result == HTTPRequest.RESULT_SUCCESS and response_code == 200 and body.size() > 0:
		var img := Image.new()
		var load_ok: int = ERR_INVALID_DATA
		if cache_key.to_lower().ends_with(".webp"):
			load_ok = img.load_webp_from_buffer(body)
		else:
			load_ok = img.load_png_from_buffer(body)
		if load_ok == OK:
			tex = ImageTexture.create_from_image(img)
			if cache_key != "":
				texture_cache[cache_key] = tex
	if cache_key != "":
		_finish_http_job(cache_key, tex)
	_pump_http_queue()

func _finish_http_job(cache_key: String, tex: Texture2D):
	var cbs: Array = _in_flight_callbacks.get(cache_key, [])
	_in_flight_callbacks.erase(cache_key)
	for cb in cbs:
		if cb is Callable:
			cb.call(tex)

func generate_demo_layout():
	clear_garden()
	# Curated Zen Garden layout showcasing root anchor, 360-deg shadows, lantern radial shadows, and canopy occlusion
	add_plant("plant_0164", Vector2(560, 380))
	add_plant("plant_0000", Vector2(650, 430))

	add_plant("plant_0353", Vector2(390, 290))
	add_plant("plant_0018", Vector2(475, 340))

	add_plant("plant_0304", Vector2(820, 520))
	add_plant("plant_0022", Vector2(910, 575))

	add_plant("plant_0067", Vector2(1050, 340))
	add_plant("plant_0038", Vector2(1135, 390))

	add_plant("plant_0001", Vector2(520, 660))
	add_plant("plant_0010", Vector2(610, 705))

	add_plant("plant_0022", Vector2(860, 270))
	add_plant("plant_0018", Vector2(1140, 670))
	if ground_canvas:
		ground_canvas.queue_redraw()

# UI SIGNAL HANDLERS
@onready var bg = $Background
@onready var sun_angle_slider = $HUD/MarginContainer/VBoxContainer/SunAngleSlider
@onready var sun_length_slider = $HUD/MarginContainer/VBoxContainer/SunLengthSlider
@onready var lantern_slider = $HUD/MarginContainer/VBoxContainer/LanternIntensitySlider

func _ready_ui():
	var btn_day = $HUD/MarginContainer/VBoxContainer/WeatherHBox/BtnDay
	var btn_sunset = $HUD/MarginContainer/VBoxContainer/WeatherHBox/BtnSunset
	var btn_night = $HUD/MarginContainer/VBoxContainer/WeatherHBox/BtnNight
	var btn_dark = $HUD/MarginContainer/VBoxContainer/WeatherHBox/BtnDark

	btn_day.pressed.connect(set_weather_day)
	btn_sunset.pressed.connect(set_weather_sunset)
	btn_night.pressed.connect(set_weather_night)
	btn_dark.pressed.connect(set_weather_dark)

	sun_angle_slider.value_changed.connect(func(v): sun_angle_deg = v)
	sun_length_slider.value_changed.connect(func(v): sun_length = v)
	lantern_slider.value_changed.connect(func(v): lantern_intensity = v)

	var vbox = $HUD/MarginContainer/VBoxContainer

	for sp_name in ["Spacer", "Spacer2", "Spacer3"]:
		var sp = vbox.get_node_or_null(sp_name)
		if sp:
			sp.custom_minimum_size.y = 6

	perspective_label = Label.new()
	perspective_label.name = "PerspectiveLabel"
	perspective_label.text = "Perspective Depth (%.2fx)" % perspective_strength
	vbox.add_child(perspective_label)

	perspective_slider = HSlider.new()
	perspective_slider.name = "PerspectiveSlider"
	perspective_slider.min_value = 0.0
	perspective_slider.max_value = 1.5
	perspective_slider.step = 0.05
	perspective_slider.value = perspective_strength
	vbox.add_child(perspective_slider)

	var insert_idx: int = lantern_slider.get_index() + 1
	vbox.move_child(perspective_label, insert_idx)
	vbox.move_child(perspective_slider, insert_idx + 1)

	perspective_slider.value_changed.connect(func(v: float):
		perspective_strength = v
		perspective_label.text = "Perspective Depth (%.2fx)" % perspective_strength
		_refresh_all_plants_perspective()
	)

	pitch_label = Label.new()
	pitch_label.name = "PitchLabel"
	pitch_label.text = "Camera Pitch: %d deg (%.2f)" % [int(round(rad_to_deg(asin(camera_pitch_ratio)))), camera_pitch_ratio]
	vbox.add_child(pitch_label)

	pitch_slider = HSlider.new()
	pitch_slider.name = "PitchSlider"
	pitch_slider.min_value = 0.50
	pitch_slider.max_value = 0.88
	pitch_slider.step = 0.02
	pitch_slider.value = camera_pitch_ratio
	vbox.add_child(pitch_slider)

	pitch_slider.value_changed.connect(func(v: float):
		camera_pitch_ratio = clampf(v, 0.45, 0.92)
		if pitch_label:
			pitch_label.text = "Camera Pitch: %d deg (%.2f)" % [int(round(rad_to_deg(asin(camera_pitch_ratio)))), camera_pitch_ratio]
		_refresh_courtyard_projection()
	)

	orbit_label = Label.new()
	orbit_label.name = "OrbitLabel"
	orbit_label.text = "Courtyard Angle: 0 deg [Q/E]"
	vbox.add_child(orbit_label)

	var orbit_row := HBoxContainer.new()
	orbit_row.name = "OrbitRow"
	vbox.add_child(orbit_row)

	var btn_orbit_left := Button.new()
	btn_orbit_left.name = "BtnOrbitLeft"
	btn_orbit_left.text = "<< Q 45"
	btn_orbit_left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	btn_orbit_left.pressed.connect(func(): _set_orbit_target(target_orbit_deg - 45.0, true))
	orbit_row.add_child(btn_orbit_left)

	var btn_orbit_reset := Button.new()
	btn_orbit_reset.name = "BtnOrbitReset"
	btn_orbit_reset.text = "Reset 0"
	btn_orbit_reset.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	btn_orbit_reset.pressed.connect(func(): _set_orbit_target(0.0, true))
	orbit_row.add_child(btn_orbit_reset)

	var btn_orbit_right := Button.new()
	btn_orbit_right.name = "BtnOrbitRight"
	btn_orbit_right.text = "45 E >>"
	btn_orbit_right.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	btn_orbit_right.pressed.connect(func(): _set_orbit_target(target_orbit_deg + 45.0, true))
	orbit_row.add_child(btn_orbit_right)

	orbit_slider = HSlider.new()
	orbit_slider.name = "OrbitSlider"
	orbit_slider.min_value = -180.0
	orbit_slider.max_value = 180.0
	orbit_slider.step = 1.0
	orbit_slider.value = 0.0
	vbox.add_child(orbit_slider)

	orbit_slider.value_changed.connect(func(v: float):
		if not _syncing_orbit_ui:
			_set_orbit_target(v, true)
	)

	var cam_idx: int = perspective_slider.get_index() + 1
	vbox.move_child(pitch_label, cam_idx)
	vbox.move_child(pitch_slider, cam_idx + 1)
	vbox.move_child(orbit_label, cam_idx + 2)
	vbox.move_child(orbit_row, cam_idx + 3)
	vbox.move_child(orbit_slider, cam_idx + 4)

	var cr = $HUD/MarginContainer/VBoxContainer/ColorRed
	var cg = $HUD/MarginContainer/VBoxContainer/ColorGreen
	var cb = $HUD/MarginContainer/VBoxContainer/ColorBlue

	for col_picker in [cr, cg, cb]:
		if col_picker:
			col_picker.custom_minimum_size.y = 22

	cr.color_changed.connect(func(c): color_red = c; update_plant_colors())
	cg.color_changed.connect(func(c): color_green = c; update_plant_colors())
	cb.color_changed.connect(func(c): color_blue = c; update_plant_colors())

	$HUD/MarginContainer/VBoxContainer/BtnRandomLayout.pressed.connect(func():
		if planting_species_id != "":
			set_planting_mode("")
		if turntable_lab_active:
			exit_turntable_lab(false)
		if video_lab_active:
			exit_video_turntable_lab(false)
		if video_gallery_active:
			exit_video_gallery(false)
		generate_random_garden()
	)
	$HUD/MarginContainer/VBoxContainer/BtnDemoLayout.pressed.connect(func():
		if planting_species_id != "":
			set_planting_mode("")
		if turntable_lab_active:
			exit_turntable_lab(false)
		if video_lab_active:
			exit_video_turntable_lab(false)
		if video_gallery_active:
			exit_video_gallery(false)
		generate_demo_layout()
	)

	btn_toggle_footprints = Button.new()
	btn_toggle_footprints.name = "BtnToggleFootprints"
	btn_toggle_footprints.text = "Footprints: ON"
	btn_toggle_footprints.custom_minimum_size.y = 24
	btn_toggle_footprints.pressed.connect(_on_toggle_footprints_pressed)
	vbox.add_child(btn_toggle_footprints)

	btn_toggle_xray = Button.new()
	btn_toggle_xray.name = "BtnToggleXRay"
	btn_toggle_xray.text = "X-Ray Peek: ON"
	btn_toggle_xray.custom_minimum_size.y = 24
	btn_toggle_xray.pressed.connect(_on_toggle_xray_pressed)
	vbox.add_child(btn_toggle_xray)

	btn_turntable_lab = Button.new()
	btn_turntable_lab.name = "BtnTurntableLab"
	btn_turntable_lab.text = "3A Turntable Lab: OFF"
	btn_turntable_lab.custom_minimum_size.y = 24
	btn_turntable_lab.pressed.connect(_on_turntable_lab_pressed)
	vbox.add_child(btn_turntable_lab)

	var turntable_ctrl_row := HBoxContainer.new()
	turntable_ctrl_row.name = "TurntableCtrlRow"
	turntable_ctrl_row.custom_minimum_size.y = 24
	vbox.add_child(turntable_ctrl_row)

	btn_turntable_dirs = Button.new()
	btn_turntable_dirs.name = "BtnTurntableDirs"
	btn_turntable_dirs.text = "Angles: 8-Way"
	btn_turntable_dirs.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	btn_turntable_dirs.custom_minimum_size.y = 24
	btn_turntable_dirs.pressed.connect(_on_turntable_dirs_pressed)
	turntable_ctrl_row.add_child(btn_turntable_dirs)

	btn_turntable_transition = Button.new()
	btn_turntable_transition.name = "BtnTurntableTransition"
	btn_turntable_transition.text = "Feel: Cross-Fade"
	btn_turntable_transition.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	btn_turntable_transition.custom_minimum_size.y = 24
	btn_turntable_transition.pressed.connect(_on_turntable_transition_pressed)
	turntable_ctrl_row.add_child(btn_turntable_transition)

	btn_video_lab = Button.new()
	btn_video_lab.name = "BtnVideoLab"
	btn_video_lab.text = "Video 36F Lab: OFF"
	btn_video_lab.custom_minimum_size.y = 24
	btn_video_lab.pressed.connect(_on_video_lab_pressed)
	vbox.add_child(btn_video_lab)

	btn_video_gallery = Button.new()
	btn_video_gallery.name = "BtnVideoGallery"
	btn_video_gallery.text = "Video 36F Gallery: OFF"
	btn_video_gallery.custom_minimum_size.y = 24
	btn_video_gallery.pressed.connect(_on_video_gallery_pressed)
	vbox.add_child(btn_video_gallery)

	fleet_status_label = Label.new()
	fleet_status_label.name = "FleetStatusLabel"
	fleet_status_label.text = "Fleet: loading manifest..."
	vbox.add_child(fleet_status_label)

func _transition_mode_name() -> String:
	if turntable_transition_mode == 1:
		return "Cross-Fade"
	elif turntable_transition_mode == 2:
		return "Pop-Flip"
	return "Crisp Snap"

func _update_lab_status_label():
	if not fleet_status_label:
		return
	if video_gallery_active:
		fleet_status_label.text = "Video 36F Gallery: %d species | orbit %d deg" % [active_plants.size(), int(round(orbit_angle_deg))]
	elif video_lab_active:
		fleet_status_label.text = "Video 36F Lab: 1-View vs 8-Way vs 36F | orbit %d deg" % int(round(orbit_angle_deg))
	elif turntable_lab_active:
		fleet_status_label.text = "3A Lab: %d-Way | %s | orbit %d deg" % [turntable_sector_count, _transition_mode_name(), int(round(orbit_angle_deg))]

func _on_turntable_lab_pressed():
	if turntable_lab_active:
		exit_turntable_lab(true)
	else:
		enter_turntable_lab()

func _on_turntable_dirs_pressed():
	turntable_sector_count = 4 if turntable_sector_count >= 8 else 8
	if btn_turntable_dirs:
		btn_turntable_dirs.text = "Angles: 8-Way" if turntable_sector_count >= 8 else "Angles: 4-Way"
	for p in active_plants:
		if is_instance_valid(p) and p.is_turntable:
			p.turntable_sector_count = turntable_sector_count
			p.update_turntable_orbit(orbit_angle_deg, 0.0, true)
	if turntable_lab_active or video_lab_active:
		_update_lab_status_label()

func _on_turntable_transition_pressed():
	if turntable_transition_mode == 1:
		turntable_transition_mode = 2
	elif turntable_transition_mode == 2:
		turntable_transition_mode = 0
	else:
		turntable_transition_mode = 1
	if btn_turntable_transition:
		btn_turntable_transition.text = "Feel: %s" % _transition_mode_name()
	for p in active_plants:
		if is_instance_valid(p) and p.is_turntable:
			p.turntable_transition_mode = turntable_transition_mode
			p.update_turntable_orbit(orbit_angle_deg, 0.0, false)
	if turntable_lab_active or video_lab_active:
		_update_lab_status_label()

func _load_json_asset(rel_path: String, cb: Callable):
	if OS.has_feature("web"):
		if web_base_url == "":
			var evaluated = JavaScriptBridge.eval("new URL('../', window.location.href).href", true)
			web_base_url = str(evaluated) if evaluated != null else ""
			if web_base_url != "" and not web_base_url.ends_with("/"):
				web_base_url += "/"
		var req := HTTPRequest.new()
		add_child(req)
		req.request_completed.connect(func(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray):
			req.queue_free()
			if result == HTTPRequest.RESULT_SUCCESS and response_code == 200:
				cb.call(JSON.parse_string(body.get_string_from_utf8()))
				return
			cb.call(null)
		)
		if req.request(web_base_url + rel_path + "?v=" + LAB_ASSET_VER) != OK:
			req.queue_free()
			cb.call(null)
		return

	var local_candidates = [
		"res://../src/" + rel_path,
		ProjectSettings.globalize_path("res://").path_join("../src/" + rel_path).simplify_path()
	]
	for path in local_candidates:
		if FileAccess.file_exists(path):
			var json_text: String = FileAccess.get_file_as_string(path)
			cb.call(JSON.parse_string(json_text))
			return
	cb.call(null)

func _on_video_lab_pressed():
	if video_lab_active:
		exit_video_turntable_lab(true)
	else:
		enter_video_turntable_lab()

func enter_video_turntable_lab():
	if planting_species_id != "":
		set_planting_mode("")
	if turntable_lab_active:
		turntable_lab_active = false
		_pending_url_lab = false
		if btn_turntable_lab:
			btn_turntable_lab.text = "3A Turntable Lab: OFF"
	if video_gallery_active:
		video_gallery_active = false
		_pending_url_video_gallery = false
		if btn_video_gallery:
			btn_video_gallery.text = "Video 36F Gallery: OFF"
	video_lab_active = true
	_pending_url_video_lab = false
	if btn_video_lab:
		btn_video_lab.text = "Video 36F Lab: ON"

	if not turntable_index_data.is_empty() and not video_atlas_meta.is_empty():
		_populate_video_turntable_lab()
		return

	if fleet_status_label:
		fleet_status_label.text = "Video 36F Lab: loading assets..."

	if turntable_index_data.is_empty() and not _turntable_index_loading:
		_turntable_index_loading = true
		_load_json_asset("assets/turntable_lab/index.json", func(parsed):
			_turntable_index_loading = false
			if parsed is Dictionary and parsed.get("species") is Array:
				turntable_index_data = parsed
			if video_lab_active and not turntable_index_data.is_empty() and not video_atlas_meta.is_empty():
				_populate_video_turntable_lab()
			elif fleet_status_label and video_lab_active and turntable_index_data.is_empty():
				fleet_status_label.text = "Video 36F Lab: 3A index load failed"
		)

	if video_atlas_meta.is_empty() and not _video_meta_loading:
		_video_meta_loading = true
		_load_json_asset("assets/turntable_lab/plant_0000_video36/metadata.json", func(parsed):
			_video_meta_loading = false
			if parsed is Dictionary and parsed.has("atlas_file"):
				video_atlas_meta = parsed
			if video_lab_active and not turntable_index_data.is_empty() and not video_atlas_meta.is_empty():
				_populate_video_turntable_lab()
			elif fleet_status_label and video_lab_active and video_atlas_meta.is_empty():
				fleet_status_label.text = "Video 36F Lab: metadata load failed"
		)

func exit_video_turntable_lab(restore_garden: bool = true):
	video_lab_active = false
	_pending_url_video_lab = false
	if btn_video_lab:
		btn_video_lab.text = "Video 36F Lab: OFF"
	if fleet_status_label:
		if not manifest_records.is_empty():
			fleet_status_label.text = "Fleet: %d species loaded" % manifest_records.size()
		else:
			fleet_status_label.text = "Fleet: 10 fallback species"
	if restore_garden:
		generate_random_garden(36)

func _on_video_gallery_pressed():
	if video_gallery_active:
		exit_video_gallery(true)
	else:
		enter_video_gallery()

func enter_video_gallery():
	if planting_species_id != "":
		set_planting_mode("")
	if turntable_lab_active:
		turntable_lab_active = false
		_pending_url_lab = false
		if btn_turntable_lab:
			btn_turntable_lab.text = "3A Turntable Lab: OFF"
	if video_lab_active:
		video_lab_active = false
		_pending_url_video_lab = false
		if btn_video_lab:
			btn_video_lab.text = "Video 36F Lab: OFF"
	video_gallery_active = true
	_pending_url_video_gallery = false
	if btn_video_gallery:
		btn_video_gallery.text = "Video 36F Gallery: ON"

	if not video_gallery_index.is_empty():
		_populate_video_gallery()
		return

	if fleet_status_label:
		fleet_status_label.text = "Video 36F Gallery: loading index..."

	if not _video_gallery_loading:
		_video_gallery_loading = true
		_load_json_asset("assets/turntable_lab/video36_index.json", func(parsed):
			_video_gallery_loading = false
			if parsed is Dictionary and parsed.get("species") is Array:
				video_gallery_index = parsed
				if video_gallery_active:
					_populate_video_gallery()
					return
			if fleet_status_label and video_gallery_active:
				fleet_status_label.text = "Video 36F Gallery: index load failed"
		)

func exit_video_gallery(restore_garden: bool = true):
	video_gallery_active = false
	_pending_url_video_gallery = false
	if btn_video_gallery:
		btn_video_gallery.text = "Video 36F Gallery: OFF"
	if fleet_status_label:
		if not manifest_records.is_empty():
			fleet_status_label.text = "Fleet: %d species loaded" % manifest_records.size()
		else:
			fleet_status_label.text = "Fleet: 10 fallback species"
	if restore_garden:
		generate_random_garden(36)

func _populate_video_gallery():
	clear_garden()
	var species_list: Array = video_gallery_index.get("species", [])
	var slots: Array = [
		Vector2(-210.0, -140.0),
		Vector2(210.0, -140.0),
		Vector2(-210.0, 145.0),
		Vector2(210.0, 145.0)
	]
	var extra_total: int = max(1, species_list.size() - slots.size())
	for idx in range(species_list.size()):
		var sp = species_list[idx]
		if not (sp is Dictionary):
			continue
		var c_pos: Vector2
		if idx < slots.size():
			c_pos = slots[idx]
		else:
			var ang: float = (float(idx - slots.size()) / float(extra_total)) * TAU
			c_pos = Vector2(cos(ang) * 290.0, sin(ang) * 290.0)
		var target_h: float = float(sp.get("gallery_height_px", 150.0))
		var cap: String = str(sp.get("label_ascii", sp.get("plant_id", "Plant")))
		_spawn_video_atlas_lab_plant(sp, c_pos, target_h, 0.0, cap)

	_update_lab_status_label()
	if ground_canvas:
		ground_canvas.queue_redraw()

func _populate_video_turntable_lab():
	clear_garden()
	var species_map: Dictionary = {}
	for sp in turntable_index_data.get("species", []):
		if sp is Dictionary and sp.has("name"):
			species_map[str(sp["name"])] = sp

	var sp_cal: Dictionary = species_map.get("plant_0000", {})
	if not sp_cal.is_empty():
		_spawn_legacy_lab_plant(sp_cal, Vector2(-240.0, 100.0), 150.0, "Calathea [Legacy 1-View]")
		_spawn_turntable_lab_plant(sp_cal, Vector2(0.0, 100.0), 150.0, 0.0, "Calathea [8-Way 3A]")

	if not video_atlas_meta.is_empty():
		_spawn_video_atlas_lab_plant(video_atlas_meta, Vector2(240.0, 100.0), 150.0, 0.0, "Calathea")
		_spawn_video_atlas_lab_plant(video_atlas_meta, Vector2(0.0, -165.0), 150.0, 180.0, "Calathea Back")

	_update_lab_status_label()
	if ground_canvas:
		ground_canvas.queue_redraw()

func _spawn_video_atlas_lab_plant(meta: Dictionary, c_pos: Vector2, target_h: float, yaw_deg: float, caption_pfx: String):
	var proj: Dictionary = project_courtyard_point(c_pos)
	var pos: Vector2 = proj["screen_pos"]
	var depth_s: float = proj["depth_scale"]
	var root_w: float = float(meta.get("root_width", 22.0))
	var sp_name: String = str(meta.get("plant_id", "plant_0000"))

	var p = plant_scene.instantiate()
	p.plant_id = sp_name
	p.skip_auto_load = true
	p.is_turntable = true
	p.is_video_atlas = true
	p.base_yaw_deg = yaw_deg
	p.caption_prefix = caption_pfx
	p.desired_height_px = target_h
	p.meta_root_width = root_w
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	p.ground_pitch_ratio = camera_pitch_ratio
	p.courtyard_pos = c_pos
	p.has_courtyard_pos = true
	p.perspective_scale = depth_s
	p.set_meta("gen_id", generation_id)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	p.set_lab_caption("%s [Video 36F: loading]" % caption_pfx)
	active_plants.append(p)

	var path_prefix: String = str(meta.get("path_prefix", "assets/turntable_lab/plant_0000_video36"))
	var atlas_file: String = str(meta.get("atlas_file", "atlas_36.webp"))
	var hf: int = int(meta.get("hframes", 6))
	var vf: int = int(meta.get("vframes", 6))
	var fc: int = int(meta.get("frame_count", 36))
	var crown_h: float = float(meta.get("crown_h", 188.0))
	var raw_anchor = meta.get("root_anchor_px", [110.0, 212.0])
	var anchor_px := Vector2(110.0, 212.0)
	if raw_anchor is Array and raw_anchor.size() >= 2:
		anchor_px = Vector2(float(raw_anchor[0]), float(raw_anchor[1]))

	var my_gen_id: int = generation_id
	_load_layer_texture(path_prefix, atlas_file, func(tex: Texture2D):
		if is_instance_valid(p) and my_gen_id == generation_id and tex != null:
			p.setup_video_atlas_turntable(sp_name, tex, hf, vf, fc, anchor_px, crown_h, target_h, root_w)
			p.update_turntable_orbit(orbit_angle_deg, 0.0, true)
			p.set_ground_pitch(camera_pitch_ratio)
			var cur_proj: Dictionary = project_courtyard_point(p.courtyard_pos)
			p.global_position = cur_proj["screen_pos"]
			p.set_perspective_scale(cur_proj["depth_scale"])
			p.show_footprint_ring = footprints_visible
			update_plant_colors(p)
	)
	return p

func enter_turntable_lab():
	if planting_species_id != "":
		set_planting_mode("")
	if video_lab_active:
		video_lab_active = false
		_pending_url_video_lab = false
		if btn_video_lab:
			btn_video_lab.text = "Video 36F Lab: OFF"
	if video_gallery_active:
		video_gallery_active = false
		_pending_url_video_gallery = false
		if btn_video_gallery:
			btn_video_gallery.text = "Video 36F Gallery: OFF"
	turntable_lab_active = true
	_pending_url_lab = false
	if btn_turntable_lab:
		btn_turntable_lab.text = "3A Turntable Lab: ON"

	if not turntable_index_data.is_empty():
		_populate_turntable_lab()
		return

	if OS.has_feature("web"):
		if web_base_url == "":
			var evaluated = JavaScriptBridge.eval("new URL('../', window.location.href).href", true)
			web_base_url = str(evaluated) if evaluated != null else ""
			if web_base_url != "" and not web_base_url.ends_with("/"):
				web_base_url += "/"
		if _turntable_index_loading:
			return
		_turntable_index_loading = true
		if fleet_status_label:
			fleet_status_label.text = "3A Lab: loading turntable index..."
		var index_url: String = web_base_url + "assets/turntable_lab/index.json"
		var req := HTTPRequest.new()
		add_child(req)
		req.request_completed.connect(func(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray):
			req.queue_free()
			_turntable_index_loading = false
			if result == HTTPRequest.RESULT_SUCCESS and response_code == 200:
				var parsed = JSON.parse_string(body.get_string_from_utf8())
				if parsed is Dictionary and parsed.get("species") is Array:
					turntable_index_data = parsed
					if turntable_lab_active:
						_populate_turntable_lab()
					return
			if fleet_status_label and turntable_lab_active:
				fleet_status_label.text = "3A Lab: index load failed"
		)
		var err = req.request(index_url)
		if err != OK:
			req.queue_free()
			_turntable_index_loading = false
			if fleet_status_label:
				fleet_status_label.text = "3A Lab: index request error"
		return

	var local_candidates = [
		"res://../src/assets/turntable_lab/index.json",
		ProjectSettings.globalize_path("res://").path_join("../src/assets/turntable_lab/index.json").simplify_path()
	]
	for path in local_candidates:
		if FileAccess.file_exists(path):
			var json_text: String = FileAccess.get_file_as_string(path)
			var parsed = JSON.parse_string(json_text)
			if parsed is Dictionary and parsed.get("species") is Array:
				turntable_index_data = parsed
				_populate_turntable_lab()
				return

	if fleet_status_label:
		fleet_status_label.text = "3A Lab: index.json missing"

func exit_turntable_lab(restore_garden: bool = true):
	turntable_lab_active = false
	_pending_url_lab = false
	if btn_turntable_lab:
		btn_turntable_lab.text = "3A Turntable Lab: OFF"
	if fleet_status_label:
		if not manifest_records.is_empty():
			fleet_status_label.text = "Fleet: %d species loaded" % manifest_records.size()
		else:
			fleet_status_label.text = "Fleet: 10 fallback species"
	if restore_garden:
		generate_random_garden(36)

func _populate_turntable_lab():
	clear_garden()
	var species_map: Dictionary = {}
	for sp in turntable_index_data.get("species", []):
		if sp is Dictionary and sp.has("name"):
			species_map[str(sp["name"])] = sp

	var sp_cal: Dictionary = species_map.get("plant_0000", {})
	var sp_dat: Dictionary = species_map.get("plant_0177", {})

	if not sp_cal.is_empty():
		_spawn_legacy_lab_plant(sp_cal, Vector2(-195.0, 135.0), 150.0, "Calathea [Legacy 1-View]")
		_spawn_turntable_lab_plant(sp_cal, Vector2(195.0, 135.0), 150.0, 0.0, "Calathea")
		_spawn_turntable_lab_plant(sp_cal, Vector2(0.0, 235.0), 135.0, 90.0, "Calathea")

	if not sp_dat.is_empty():
		_spawn_legacy_lab_plant(sp_dat, Vector2(-195.0, -145.0), 185.0, "Datura [Legacy 1-View]")
		_spawn_turntable_lab_plant(sp_dat, Vector2(195.0, -145.0), 185.0, 0.0, "Datura")
		_spawn_turntable_lab_plant(sp_dat, Vector2(0.0, -235.0), 175.0, 180.0, "Datura")

	_update_lab_status_label()
	if ground_canvas:
		ground_canvas.queue_redraw()

func _spawn_legacy_lab_plant(sp: Dictionary, c_pos: Vector2, target_h: float, caption: String):
	var legacy: Dictionary = sp.get("legacy", {})
	if legacy.is_empty():
		return null
	var proj: Dictionary = project_courtyard_point(c_pos)
	var pos: Vector2 = proj["screen_pos"]
	var depth_s: float = proj["depth_scale"]
	var root_w: float = float(legacy.get("root_width", sp.get("root_width", 24.0)))
	var raw_h: float = max(float(legacy.get("height", 250.0)), 1.0)
	var fp_w: float = max(10.0, root_w * (target_h / raw_h) * depth_s)
	var fp_h: float = max(6.0, fp_w * camera_pitch_ratio)

	var p = plant_scene.instantiate()
	var rec_id: String = str(legacy.get("name", sp.get("name", "unknown")))
	p.plant_id = rec_id
	p.skip_auto_load = true
	p.is_turntable = false
	p.desired_height_px = target_h
	p.meta_root_width = root_w
	p.fp_w = fp_w
	p.fp_h = fp_h
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	p.ground_pitch_ratio = camera_pitch_ratio
	p.courtyard_pos = c_pos
	p.has_courtyard_pos = true
	p.perspective_scale = depth_s
	p.set_meta("gen_id", generation_id)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	p.set_lab_caption(caption)
	active_plants.append(p)

	var path_prefix: String = str(legacy.get("path_prefix", ""))
	var slot_files: Dictionary = {}
	var layer_files = legacy.get("layer_files", [])
	if layer_files is Array:
		for lf in layer_files:
			if lf is Dictionary:
				var slot: String = str(lf.get("slot", ""))
				var fname: String = str(lf.get("file", ""))
				if slot in ["red", "green", "blue"] and fname != "":
					slot_files[slot] = fname
	if slot_files.is_empty():
		return p

	var anchor_uv: Vector2 = Vector2(-1.0, -1.0)
	var raw_anchor = legacy.get("root_anchor", null)
	if raw_anchor is Array and raw_anchor.size() >= 2:
		anchor_uv = Vector2(float(raw_anchor[0]), float(raw_anchor[1]))

	var my_gen_id: int = generation_id
	var loaded_textures: Dictionary = {"red": null, "green": null, "blue": null}
	var remaining: Array = [slot_files.size()]
	for slot in slot_files.keys():
		_load_layer_texture(path_prefix, slot_files[slot], func(tex: Texture2D):
			loaded_textures[slot] = tex
			remaining[0] -= 1
			if remaining[0] == 0:
				if is_instance_valid(p) and my_gen_id == generation_id:
					if loaded_textures["red"] or loaded_textures["green"] or loaded_textures["blue"]:
						p.setup_with_textures(
							rec_id,
							loaded_textures["red"],
							loaded_textures["green"],
							loaded_textures["blue"],
							target_h,
							root_w,
							anchor_uv
						)
						p.set_ground_pitch(camera_pitch_ratio)
						var cur_proj: Dictionary = project_courtyard_point(p.courtyard_pos)
						p.global_position = cur_proj["screen_pos"]
						p.set_perspective_scale(cur_proj["depth_scale"])
						p.show_footprint_ring = footprints_visible
						update_plant_colors(p)
		)
	return p

func _spawn_turntable_lab_plant(sp: Dictionary, c_pos: Vector2, target_h: float, yaw_deg: float, caption_prefix: String):
	var proj: Dictionary = project_courtyard_point(c_pos)
	var pos: Vector2 = proj["screen_pos"]
	var depth_s: float = proj["depth_scale"]
	var root_w: float = float(sp.get("root_width", 24.0))
	var sp_name: String = str(sp.get("name", "plant_0000"))

	var p = plant_scene.instantiate()
	p.plant_id = sp_name
	p.skip_auto_load = true
	p.is_turntable = true
	p.base_yaw_deg = yaw_deg
	p.turntable_sector_count = turntable_sector_count
	p.turntable_transition_mode = turntable_transition_mode
	p.caption_prefix = caption_prefix
	p.desired_height_px = target_h
	p.meta_root_width = root_w
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	p.ground_pitch_ratio = camera_pitch_ratio
	p.courtyard_pos = c_pos
	p.has_courtyard_pos = true
	p.perspective_scale = depth_s
	p.set_meta("gen_id", generation_id)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	p.set_lab_caption("%s [0 deg]" % caption_prefix)
	active_plants.append(p)

	var views: Dictionary = sp.get("views", {})
	var my_gen_id: int = generation_id
	for ang_key in views.keys():
		var view: Dictionary = views[ang_key]
		var ang_int: int = int(ang_key)
		var path_prefix: String = str(view.get("path_prefix", ""))
		var crown_h: float = float(view.get("crown_h", -1.0))
		var anchor_uv: Vector2 = Vector2(-1.0, -1.0)
		var raw_anchor = view.get("root_anchor", null)
		if raw_anchor is Array and raw_anchor.size() >= 2:
			anchor_uv = Vector2(float(raw_anchor[0]), float(raw_anchor[1]))

		var slot_files: Dictionary = {}
		var layer_files = view.get("layer_files", [])
		if layer_files is Array:
			for lf in layer_files:
				if lf is Dictionary:
					var slot: String = str(lf.get("slot", ""))
					var fname: String = str(lf.get("file", ""))
					if slot in ["red", "green", "blue"] and fname != "":
						slot_files[slot] = fname
		if slot_files.is_empty():
			continue

		var loaded_textures: Dictionary = {"red": null, "green": null, "blue": null}
		var remaining: Array = [slot_files.size()]
		for slot in slot_files.keys():
			_load_layer_texture(path_prefix, slot_files[slot], func(tex: Texture2D):
				loaded_textures[slot] = tex
				remaining[0] -= 1
				if remaining[0] == 0:
					if is_instance_valid(p) and my_gen_id == generation_id:
						if loaded_textures["red"] or loaded_textures["green"] or loaded_textures["blue"]:
							p.register_turntable_frame(
								ang_int,
								loaded_textures["red"],
								loaded_textures["green"],
								loaded_textures["blue"],
								anchor_uv,
								crown_h
							)
							p.update_turntable_orbit(orbit_angle_deg, 0.0, true)
							p.set_ground_pitch(camera_pitch_ratio)
							var cur_proj: Dictionary = project_courtyard_point(p.courtyard_pos)
							p.global_position = cur_proj["screen_pos"]
							p.set_perspective_scale(cur_proj["depth_scale"])
							p.show_footprint_ring = footprints_visible
							update_plant_colors(p)
			)
	return p

func _on_toggle_footprints_pressed():
	footprints_visible = not footprints_visible
	if btn_toggle_footprints:
		btn_toggle_footprints.text = "Footprints: ON" if footprints_visible else "Footprints: OFF"
	for p in active_plants:
		if is_instance_valid(p):
			p.show_footprint_ring = footprints_visible

func _on_toggle_xray_pressed():
	xray_enabled = not xray_enabled
	if btn_toggle_xray:
		btn_toggle_xray.text = "X-Ray Peek: ON" if xray_enabled else "X-Ray Peek: OFF"

func set_weather_day():
	bg.color = Color("#9e8f63")
	sun_angle_slider.value = 45.0
	sun_length_slider.value = 0.8
	if ground_canvas:
		ground_canvas.queue_redraw()

func set_weather_sunset():
	bg.color = Color("#8c6a51")
	sun_angle_slider.value = 15.0
	sun_length_slider.value = 1.6
	if ground_canvas:
		ground_canvas.queue_redraw()

func set_weather_night():
	bg.color = Color("#1e2a38")
	sun_angle_slider.value = 90.0
	sun_length_slider.value = 0.3
	if ground_canvas:
		ground_canvas.queue_redraw()

func set_weather_dark():
	bg.color = Color("#0a0a0a")
	sun_angle_slider.value = 0.0
	sun_length_slider.value = 0.0
	if ground_canvas:
		ground_canvas.queue_redraw()

func _exit_all_labs(restore_garden: bool = true):
	if turntable_lab_active:
		exit_turntable_lab(restore_garden)
		restore_garden = false
	if video_lab_active:
		exit_video_turntable_lab(restore_garden)
		restore_garden = false
	if video_gallery_active:
		exit_video_gallery(restore_garden)

func _build_plant_payload(p) -> Dictionary:
	if not is_instance_valid(p):
		return {}
	var rec: Dictionary = _manifest_by_id.get(p.plant_id, {})
	var ai = rec.get("ai_analysis", {})
	var bot_h = ai.get("botanical_height_m", null) if ai is Dictionary else null
	return {
		"uid": p.plant_uid,
		"plant_id": p.plant_id,
		"display_name": str(rec.get("display_name", p.plant_id)),
		"scientific_name": str(ai.get("scientific_name", "")) if ai is Dictionary else "",
		"plant_type": str(ai.get("plant_type", "")) if ai is Dictionary else "",
		"growth_habit": str(ai.get("growth_habit", "")) if ai is Dictionary else "",
		"botanical_height_m": bot_h,
		"brief": str(ai.get("brief", "")) if ai is Dictionary else "",
		"is_video36": p.is_video_atlas,
		"screen_x": round(p.global_position.x * 10.0) / 10.0,
		"screen_y": round(p.global_position.y * 10.0) / 10.0,
		"courtyard_x": round(p.courtyard_pos.x * 10.0) / 10.0,
		"courtyard_y": round(p.courtyard_pos.y * 10.0) / 10.0,
		"visual_height": round(p.visual_height * 10.0) / 10.0,
		"fp_w": round(p.fp_w * 10.0) / 10.0,
		"fp_h": round(p.fp_h * 10.0) / 10.0,
		"golden_sentence": p.golden_sentence,
	}

func pick_plant_at_screen_pos(screen_pos: Vector2):
	var best = null
	var best_y: float = -1e9
	for p in active_plants:
		if not is_instance_valid(p):
			continue
		if p.has_method("hit_test_screen") and p.hit_test_screen(screen_pos):
			if p.global_position.y > best_y:
				best_y = p.global_position.y
				best = p
	return best

func _select_plant(p, is_long_press: bool = false):
	if is_instance_valid(selected_plant) and selected_plant != p:
		selected_plant.is_selected = false
	selected_plant = p if is_instance_valid(p) else null
	if selected_plant != null:
		selected_plant.is_selected = true
		var ev_type: String = "plant_long_press" if is_long_press else "plant_selected"
		_emit_js_event(ev_type, _build_plant_payload(selected_plant))
	else:
		_emit_js_event("selection_cleared", {})

func set_planting_mode(species_id: String):
	var clean_id: String = species_id.strip_edges()
	if clean_id != "":
		_initial_garden_auto_seeded = false
	if clean_id != "" and (turntable_lab_active or video_lab_active or video_gallery_active):
		_exit_all_labs(true)
	planting_species_id = clean_id
	if clean_id != "" and is_instance_valid(selected_plant):
		selected_plant.is_selected = false
		selected_plant = null
	if clean_id != "" and _manifest_by_id.has(clean_id):
		var m: Dictionary = _sample_record_metrics(_manifest_by_id[clean_id])
		var ds: float = get_depth_scale(_planting_preview_pos.y if _planting_preview_pos.length_squared() > 1.0 else COURTYARD_CENTER.y)
		_planting_preview_fp_w = max(10.0, float(m["fp_w"]) * ds)
		_planting_preview_fp_h = max(6.0, _planting_preview_fp_w * camera_pitch_ratio)
	else:
		_planting_preview_fp_w = 24.0
		_planting_preview_fp_h = 24.0 * camera_pitch_ratio
	_update_planting_preview_validity()
	if ground_canvas:
		ground_canvas.queue_redraw()
	_emit_js_event("planting_mode_changed", {"plant_id": planting_species_id})

func _update_planting_preview_validity():
	if planting_species_id == "" or not _manifest_by_id.has(planting_species_id):
		_planting_preview_valid = false
		return
	if _planting_preview_pos.length_squared() <= 1.0:
		_planting_preview_valid = false
		return
	var m: Dictionary = _sample_record_metrics(_manifest_by_id[planting_species_id])
	var ds: float = get_depth_scale(_planting_preview_pos.y)
	_planting_preview_fp_w = max(10.0, float(m["fp_w"]) * ds)
	_planting_preview_fp_h = max(6.0, _planting_preview_fp_w * camera_pitch_ratio)
	_planting_preview_valid = can_place_plant(_planting_preview_pos, _planting_preview_fp_w, _planting_preview_fp_h)

func plant_species_at_screen_pos(species_id: String, screen_pos: Vector2, extra: Dictionary = {}) -> Dictionary:
	_initial_garden_auto_seeded = false
	var clean_id: String = species_id.strip_edges()
	if not _manifest_by_id.has(clean_id):
		return {"ok": false, "reason": "unknown_species", "plant_id": clean_id}
	var rec: Dictionary = _manifest_by_id[clean_id]
	var metrics: Dictionary = _sample_record_metrics(rec)
	var target_h: float = float(metrics["target_h"])
	if extra.has("height_px") and float(extra["height_px"]) > 10.0:
		target_h = clampf(float(extra["height_px"]), 35.0, 230.0)
	var raw_h: float = max(float(rec.get("height", 250.0)), 1.0)
	var root_w: float = float(metrics["root_w"])
	var fp_w: float = max(10.0, root_w * (target_h / raw_h))
	var depth_s: float = get_depth_scale(screen_pos.y)
	var eff_fp_w: float = max(10.0, fp_w * depth_s)
	var eff_fp_h: float = max(6.0, eff_fp_w * camera_pitch_ratio)
	if not bool(extra.get("force", false)) and not can_place_plant(screen_pos, eff_fp_w, eff_fp_h):
		return {"ok": false, "reason": "collision", "plant_id": clean_id}
	var p = spawn_manifest_plant(rec, screen_pos, target_h, root_w, eff_fp_w, eff_fp_h, depth_s, extra)
	placed_footprints.append({"pos": screen_pos, "fp_w": eff_fp_w, "fp_h": eff_fp_h})
	if ground_canvas:
		ground_canvas.queue_redraw()
	var payload: Dictionary = _build_plant_payload(p)
	payload["ok"] = true
	_emit_js_event("plant_placed", payload)
	return payload

func plant_species_auto(species_id: String, extra: Dictionary = {}) -> Dictionary:
	var clean_id: String = species_id.strip_edges()
	if not _manifest_by_id.has(clean_id):
		return {"ok": false, "reason": "unknown_species", "plant_id": clean_id}
	if turntable_lab_active or video_lab_active or video_gallery_active:
		_exit_all_labs(true)
	if extra.has("courtyard_x") and extra.has("courtyard_y"):
		var cpt := Vector2(float(extra["courtyard_x"]), float(extra["courtyard_y"]))
		var proj: Dictionary = project_courtyard_point(cpt)
		return plant_species_at_screen_pos(clean_id, proj["screen_pos"], extra)
	var scatter_limit: float = COURTYARD_HALF_SIZE - 35.0
	for _attempt in range(120):
		var w_pt := Vector2(randf_range(-scatter_limit, scatter_limit), randf_range(-scatter_limit, scatter_limit))
		var proj: Dictionary = project_courtyard_point(w_pt)
		var pos: Vector2 = proj["screen_pos"]
		if pos.x < 110.0 or pos.x > 1350.0 or pos.y < 130.0 or pos.y > 855.0:
			continue
		var res: Dictionary = plant_species_at_screen_pos(clean_id, pos, extra)
		if res.get("ok", false):
			return res
	return {"ok": false, "reason": "no_open_spot", "plant_id": clean_id}

func _init_js_bridge():
	if not OS.has_feature("web"):
		return
	_js_bridge_callback = JavaScriptBridge.create_callback(_on_js_bridge_command)
	var win = JavaScriptBridge.get_interface("window")
	if win:
		win.__psyGardenGodotCommand = _js_bridge_callback
	_emit_js_event("godot_ready", {"version": LAB_ASSET_VER})

func _on_js_bridge_command(args: Array):
	if args.is_empty():
		return
	var raw = args[0]
	var cmd_str: String = str(raw) if raw is String else JSON.stringify(raw)
	handle_bridge_command(cmd_str)

func _emit_js_event(event_type: String, payload: Dictionary = {}):
	if not OS.has_feature("web"):
		return
	var envelope := {"source": "psy_garden_godot", "type": event_type, "payload": payload}
	var b64: String = Marshalls.utf8_to_base64(JSON.stringify(envelope))
	var js: String = "(function(){try{var bin=atob('%s');var bytes=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);var msg=JSON.parse(new TextDecoder().decode(bytes));if(window.__psyGardenOnGodotEvent)window.__psyGardenOnGodotEvent(msg);if(window.parent&&window.parent!==window)window.parent.postMessage(msg,'*');}catch(e){console.error('Bridge emit error',e);}})()" % b64
	JavaScriptBridge.eval(js, true)

func handle_bridge_command(cmd_json: String) -> Dictionary:
	var parsed = JSON.parse_string(cmd_json)
	if not (parsed is Dictionary):
		return {"ok": false, "reason": "invalid_json"}
	var cmd_type: String = str(parsed.get("type", "")).strip_edges()
	var payload_raw = parsed.get("payload", {})
	var payload: Dictionary = payload_raw if payload_raw is Dictionary else {}
	if cmd_type == "set_planting_mode":
		var pid: String = str(payload.get("plant_id", ""))
		set_planting_mode(pid)
		return {"ok": true, "planting_species_id": planting_species_id}
	elif cmd_type == "spawn_plant":
		var pid: String = str(payload.get("plant_id", ""))
		return plant_species_auto(pid, payload)
	elif cmd_type == "clear_garden":
		_exit_all_labs(false)
		set_planting_mode("")
		clear_garden()
		_emit_js_event("garden_cleared", {})
		return {"ok": true, "plant_count": 0}
	elif cmd_type == "random_garden":
		_exit_all_labs(false)
		set_planting_mode("")
		var cnt: int = clampi(int(payload.get("count", 36)), 1, 80)
		generate_random_garden(cnt)
		_emit_js_event("garden_randomized", {"plant_count": active_plants.size()})
		return {"ok": true, "plant_count": active_plants.size()}
	elif cmd_type == "enter_video_gallery":
		set_planting_mode("")
		enter_video_gallery()
		return {"ok": true, "mode": "video_gallery"}
	elif cmd_type == "exit_video_gallery":
		exit_video_gallery(true)
		return {"ok": true, "mode": "regular"}
	elif cmd_type == "set_orbit":
		var ang: float = float(payload.get("angle_deg", 0.0))
		_set_orbit_target(ang, bool(payload.get("snap", true)))
		return {"ok": true, "orbit_deg": target_orbit_deg}
	elif cmd_type == "set_weather":
		var preset: String = str(payload.get("preset", "day")).to_lower()
		if preset == "sunset":
			set_weather_sunset()
		elif preset == "night":
			set_weather_night()
		elif preset == "dark":
			set_weather_dark()
		else:
			set_weather_day()
		return {"ok": true, "preset": preset}
	elif cmd_type == "get_state":
		var mode_str: String = "regular"
		if video_gallery_active:
			mode_str = "video_gallery"
		elif video_lab_active:
			mode_str = "video_lab"
		elif turntable_lab_active:
			mode_str = "turntable_lab"
		var plants_out: Array = []
		for p in active_plants:
			if is_instance_valid(p):
				plants_out.append(_build_plant_payload(p))
		var snap := {
			"ok": true,
			"orbit_deg": orbit_angle_deg,
			"mode": mode_str,
			"plant_count": plants_out.size(),
			"planting_species_id": planting_species_id,
			"video36_ids": video36_by_id.keys(),
			"plants": plants_out,
		}
		_emit_js_event("state_snapshot", snap)
		return snap
	return {"ok": false, "reason": "unknown_command", "type": cmd_type}
