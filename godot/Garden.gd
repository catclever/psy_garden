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

var sun_angle_deg: float = 45.0
var sun_length: float = 1.0
var lantern_intensity: float = 1.0
var lantern_radius: float = 520.0

var color_red: Color = Color.WHITE
var color_green: Color = Color.WHITE
var color_blue: Color = Color.WHITE

var fleet_status_label: Label = null
var btn_toggle_footprints: Button = null

func _ready():
	mouse_lantern.global_position = Vector2(680, 420)
	_ready_ui()
	_init_http_pool()
	generate_demo_layout()
	call_deferred("_load_manifest")

func _process(delta):
	var raw_mouse = get_global_mouse_position()
	var target_mouse = raw_mouse if raw_mouse.length_squared() > 4.0 else Vector2(680, 420)
	mouse_lantern.global_position = mouse_lantern.global_position.lerp(target_mouse, 25.0 * delta)
	var mouse_pos = mouse_lantern.global_position
	mouse_lantern_light.energy = lantern_intensity * 0.85

	var sun_rad = deg_to_rad(sun_angle_deg)
	var sun_dir = Vector2(cos(sun_rad), sin(sun_rad))
	var shadow_scale_y = 0.55

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

func clear_garden():
	generation_id += 1
	for p in active_plants:
		if is_instance_valid(p):
			p.queue_free()
	active_plants.clear()
	placed_footprints.clear()

func add_plant(id: String, pos: Vector2):
	var p = plant_scene.instantiate()
	p.plant_id = id
	p.show_footprint_ring = footprints_visible
	p.global_position = pos
	y_sort.add_child(p)
	active_plants.append(p)
	placed_footprints.append({"pos": pos, "fp_w": p.fp_w, "fp_h": p.fp_h})

	update_plant_colors(p)

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
	if pos.x < 290.0 and pos.y < 600.0:
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
		var a: float = (fp_w + float(other["fp_w"])) * 0.5 + 12.0
		var b: float = (fp_h + float(other["fp_h"])) * 0.5 + 8.0
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
	var fp_h: float = max(6.0, fp_w * 0.55)
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
			for _attempt in range(30):
				var pos = Vector2(randf_range(130.0, 1330.0), randf_range(140.0, 840.0))
				if can_place_plant(pos, 24.0, 14.0):
					add_plant(id, pos)
					break
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
		return

	for i in range(count):
		var b_key: String = bucket_order[i % bucket_order.size()]
		var pool: Array = buckets[b_key]
		var rec: Dictionary = pool[randi() % pool.size()]
		var metrics: Dictionary = _sample_record_metrics(rec)
		var fp_w: float = metrics["fp_w"]
		var fp_h: float = metrics["fp_h"]
		var target_h: float = metrics["target_h"]
		var root_w: float = metrics["root_w"]

		for _attempt in range(40):
			var pos = Vector2(randf_range(130.0, 1330.0), randf_range(140.0, 840.0))
			if can_place_plant(pos, fp_w, fp_h):
				spawn_manifest_plant(rec, pos, target_h, root_w, fp_w, fp_h)
				placed_footprints.append({"pos": pos, "fp_w": fp_w, "fp_h": fp_h})
				break

func spawn_manifest_plant(rec: Dictionary, pos: Vector2, target_h: float, root_w: float, fp_w: float, fp_h: float):
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
	p.set_meta("gen_id", generation_id)
	y_sort.add_child(p)
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
		return

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
						p.show_footprint_ring = footprints_visible
						update_plant_colors(p)
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

	var cr = $HUD/MarginContainer/VBoxContainer/ColorRed
	var cg = $HUD/MarginContainer/VBoxContainer/ColorGreen
	var cb = $HUD/MarginContainer/VBoxContainer/ColorBlue

	cr.color_changed.connect(func(c): color_red = c; update_plant_colors())
	cg.color_changed.connect(func(c): color_green = c; update_plant_colors())
	cb.color_changed.connect(func(c): color_blue = c; update_plant_colors())

	$HUD/MarginContainer/VBoxContainer/BtnRandomLayout.pressed.connect(func(): generate_random_garden())
	$HUD/MarginContainer/VBoxContainer/BtnDemoLayout.pressed.connect(generate_demo_layout)

	var vbox = $HUD/MarginContainer/VBoxContainer
	btn_toggle_footprints = Button.new()
	btn_toggle_footprints.name = "BtnToggleFootprints"
	btn_toggle_footprints.text = "Footprints: ON"
	btn_toggle_footprints.pressed.connect(_on_toggle_footprints_pressed)
	vbox.add_child(btn_toggle_footprints)

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

func set_weather_day():
	bg.color = Color("#9e8f63")
	sun_angle_slider.value = 45.0
	sun_length_slider.value = 0.8

func set_weather_sunset():
	bg.color = Color("#8c6a51")
	sun_angle_slider.value = 15.0
	sun_length_slider.value = 1.6

func set_weather_night():
	bg.color = Color("#1e2a38")
	sun_angle_slider.value = 90.0
	sun_length_slider.value = 0.3

func set_weather_dark():
	bg.color = Color("#0a0a0a")
	sun_angle_slider.value = 0.0
	sun_length_slider.value = 0.0
