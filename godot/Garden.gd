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

const MAX_CONCURRENT_HTTP: int = 8
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
	generate_demo_layout()
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
	var sy: float = COURTYARD_CENTER.y + ry * eff_pitch
	var d_scale: float = get_depth_scale(sy, eff_persp)
	var sx: float = COURTYARD_CENTER.x + rx * d_scale
	return {"screen_pos": Vector2(sx, sy), "depth_scale": d_scale, "rx": rx, "ry": ry}

func screen_to_courtyard(screen_pos: Vector2) -> Vector2:
	var d_scale: float = max(get_depth_scale(screen_pos.y), 0.25)
	var eff_pitch: float = max(camera_pitch_ratio, 0.25)
	var rx: float = (screen_pos.x - COURTYARD_CENTER.x) / d_scale
	var ry: float = (screen_pos.y - COURTYARD_CENTER.y) / eff_pitch
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

	# Karesansui raked-sand concentric rings
	var ring_radii: Array = [85.0, 135.0, 190.0, 250.0, 320.0, 395.0]
	var segs: int = 48
	for r in ring_radii:
		var pts := PackedVector2Array()
		for i in range(segs + 1):
			var ang: float = (float(i) / float(segs)) * TAU
			var pt: Vector2 = Vector2(cos(ang) * float(r), sin(ang) * float(r))
			pts.append(project_courtyard_point(pt)["screen_pos"])
		ground_canvas.draw_polyline(pts, ring_col, 1.25, true)

	# Perspective courtyard inner grid lines step 130 (subdivided 16 segments per line)
	var half_w: float = 520.0
	var half_h: float = 360.0
	var grid_segs: int = 16
	for gx in [-390.0, -260.0, -130.0, 0.0, 130.0, 260.0, 390.0]:
		var pts := PackedVector2Array()
		for i in range(grid_segs + 1):
			var t: float = float(i) / float(grid_segs)
			var gy: float = lerpf(-half_h, half_h, t)
			pts.append(project_courtyard_point(Vector2(gx, gy))["screen_pos"])
		ground_canvas.draw_polyline(pts, grid_col, 1.0, true)

	for gy in [-260.0, -130.0, 0.0, 130.0, 260.0]:
		var pts := PackedVector2Array()
		for i in range(grid_segs + 1):
			var t: float = float(i) / float(grid_segs)
			var gx: float = lerpf(-half_w, half_w, t)
			pts.append(project_courtyard_point(Vector2(gx, gy))["screen_pos"])
		ground_canvas.draw_polyline(pts, grid_col, 1.0, true)

	# Perspective courtyard timber/stone outer border [-520..520, -360..360]
	var border_corners: Array = [
		Vector2(-half_w, -half_h),
		Vector2(half_w, -half_h),
		Vector2(half_w, half_h),
		Vector2(-half_w, half_h)
	]
	var border_pts := PackedVector2Array()
	for c_idx in range(4):
		var p0: Vector2 = border_corners[c_idx]
		var p1: Vector2 = border_corners[(c_idx + 1) % 4]
		for i in range(grid_segs):
			var t: float = float(i) / float(grid_segs)
			border_pts.append(project_courtyard_point(p0.lerp(p1, t))["screen_pos"])
	border_pts.append(project_courtyard_point(border_corners[0])["screen_pos"])
	ground_canvas.draw_polyline(border_pts, border_col, 2.0, true)

	# Cardinal markers along X/Y axes
	var marker_dist_x: float = 475.0
	var marker_dist_y: float = 325.0
	var marker_len: float = 22.0
	var cardinal_segs: Array = [
		[Vector2(marker_dist_x - marker_len, 0.0), Vector2(marker_dist_x + marker_len, 0.0)],
		[Vector2(-marker_dist_x - marker_len, 0.0), Vector2(-marker_dist_x + marker_len, 0.0)],
		[Vector2(0.0, marker_dist_y - marker_len), Vector2(0.0, marker_dist_y + marker_len)],
		[Vector2(0.0, -marker_dist_y - marker_len), Vector2(0.0, -marker_dist_y + marker_len)]
	]
	for seg in cardinal_segs:
		var a: Vector2 = project_courtyard_point(seg[0])["screen_pos"]
		var b: Vector2 = project_courtyard_point(seg[1])["screen_pos"]
		ground_canvas.draw_line(a, b, axis_col, 2.0, true)
		ground_canvas.draw_circle(b, 3.0, axis_col)

func _unhandled_input(event):
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT:
			_right_dragging = event.pressed
	elif event is InputEventMouseMotion and _right_dragging:
		target_orbit_deg = _wrap_deg_180(target_orbit_deg + event.relative.x * 0.35)

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

		# Apply to B's shaders (all 3 sprites)
		for sprite in [b.sprite_red, b.sprite_green, b.sprite_blue]:
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
		if t.sprite_red and t.sprite_red.material:
			(t.sprite_red.material as ShaderMaterial).set_shader_parameter("tint_color", color_red)
		if t.sprite_green and t.sprite_green.material:
			(t.sprite_green.material as ShaderMaterial).set_shader_parameter("tint_color", color_green)
		if t.sprite_blue and t.sprite_blue.material:
			(t.sprite_blue.material as ShaderMaterial).set_shader_parameter("tint_color", color_blue)

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
	if manifest_records.is_empty():
		var fallback_count: int = min(count, 16)
		for i in range(fallback_count):
			var id = available_plants[randi() % available_plants.size()]
			for _attempt in range(80):
				var pos = Vector2(randf_range(130.0, 1330.0), randf_range(140.0, 840.0))
				var depth_s: float = get_depth_scale(pos.y)
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

	for i in range(count):
		var b_key: String = bucket_order[i % bucket_order.size()]
		var pool: Array = buckets[b_key]
		var rec: Dictionary = pool[randi() % pool.size()]
		var metrics: Dictionary = _sample_record_metrics(rec)
		var fp_w: float = metrics["fp_w"]
		var target_h: float = metrics["target_h"]
		var root_w: float = metrics["root_w"]

		for _attempt in range(80):
			var pos = Vector2(randf_range(130.0, 1330.0), randf_range(140.0, 840.0))
			var depth_s: float = get_depth_scale(pos.y)
			var eff_fp_w: float = max(10.0, fp_w * depth_s)
			var eff_fp_h: float = max(6.0, eff_fp_w * camera_pitch_ratio)
			if can_place_plant(pos, eff_fp_w, eff_fp_h):
				spawn_manifest_plant(rec, pos, target_h, root_w, eff_fp_w, eff_fp_h, depth_s)
				placed_footprints.append({"pos": pos, "fp_w": eff_fp_w, "fp_h": eff_fp_h})
				break
	if ground_canvas:
		ground_canvas.queue_redraw()

func spawn_manifest_plant(rec: Dictionary, pos: Vector2, target_h: float, root_w: float, fp_w: float, fp_h: float, depth_scale: float = 1.0):
	var p = plant_scene.instantiate()
	var rec_id: String = str(rec.get("name", rec.get("id", "unknown")))
	p.plant_id = rec_id
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
	p.set_meta("gen_id", generation_id)
	y_sort.add_child(p)
	p.set_ground_pitch(camera_pitch_ratio)
	active_plants.append(p)

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
						p.show_footprint_ring = footprints_visible
						update_plant_colors(p)
		)

	return p

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
		if fleet_status_label:
			fleet_status_label.text = "Fleet: manifest parse error"
		return
	manifest_records.clear()
	for rec in parsed["plants"]:
		if rec is Dictionary:
			if rec.get("enabled", true) and rec.get("true_color", true):
				manifest_records.append(rec)

	if fleet_status_label:
		fleet_status_label.text = "Fleet: %d species loaded" % manifest_records.size()

	if not manifest_loaded_once and not manifest_records.is_empty():
		manifest_loaded_once = true
		generate_random_garden(36)

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
	var url: String = web_base_url + cache_key
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
		if img.load_png_from_buffer(body) == OK:
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

	$HUD/MarginContainer/VBoxContainer/BtnRandomLayout.pressed.connect(func(): generate_random_garden())
	$HUD/MarginContainer/VBoxContainer/BtnDemoLayout.pressed.connect(generate_demo_layout)

	btn_toggle_footprints = Button.new()
	btn_toggle_footprints.name = "BtnToggleFootprints"
	btn_toggle_footprints.text = "Footprints: ON"
	btn_toggle_footprints.pressed.connect(_on_toggle_footprints_pressed)
	vbox.add_child(btn_toggle_footprints)

	btn_toggle_xray = Button.new()
	btn_toggle_xray.name = "BtnToggleXRay"
	btn_toggle_xray.text = "X-Ray Peek: ON"
	btn_toggle_xray.pressed.connect(_on_toggle_xray_pressed)
	vbox.add_child(btn_toggle_xray)

	fleet_status_label = Label.new()
	fleet_status_label.name = "FleetStatusLabel"
	fleet_status_label.text = "Fleet: loading manifest..."
	vbox.add_child(fleet_status_label)

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
