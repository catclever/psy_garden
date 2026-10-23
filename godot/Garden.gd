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

var sun_angle_deg: float = 45.0
var sun_length: float = 1.0
var lantern_intensity: float = 1.0
var lantern_radius: float = 520.0

var color_red: Color = Color.WHITE
var color_green: Color = Color.WHITE
var color_blue: Color = Color.WHITE

func _ready():
	mouse_lantern.global_position = Vector2(680, 420)
	_ready_ui()
	generate_demo_layout()

func _process(delta):
	var raw_mouse = get_global_mouse_position()
	var target_mouse = raw_mouse if raw_mouse.length_squared() > 4.0 else Vector2(680, 420)
	mouse_lantern.global_position = mouse_lantern.global_position.lerp(target_mouse, 25.0 * delta)
	var mouse_pos = mouse_lantern.global_position
	mouse_lantern_light.energy = lantern_intensity * 0.85

	var sun_rad = deg_to_rad(sun_angle_deg)
	var sun_dir = Vector2(cos(sun_rad), sin(sun_rad))
	var shadow_scale_y = 0.55
	var sun_shadow_world_dir = Vector2(cos(sun_rad), sin(sun_rad) * shadow_scale_y)

	# 1. Update plant shadows
	for p in active_plants:
		if not is_instance_valid(p): continue

		# Sun Shadow
		p.sun_shadow_pivot.rotation = sun_dir.angle() + PI / 2.0
		p.sun_shadow_pivot.scale = Vector2(1.0, sun_length)

		# Lantern Shadow
		var iso_delta = (p.global_position - mouse_pos) / Vector2(1.0, shadow_scale_y)
		var dist = iso_delta.length()

		if dist < lantern_radius and dist > 5.0:
			var proximity = clamp(1.0 - dist / lantern_radius, 0.0, 1.0)
			p.lantern_shadow_pivot.rotation = iso_delta.angle() + PI / 2.0
			p.lantern_shadow_pivot.scale = Vector2(1.0, lerp(shadow_scale_y, 1.85, proximity))
			var a = pow(proximity, 1.15) * 0.62 * lantern_intensity
			p.lantern_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, a))
		else:
			p.lantern_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, 0))

		# Incense Shadow
		var inc_iso = (p.global_position - incense_burner.global_position) / Vector2(1.0, shadow_scale_y)
		var inc_dist = inc_iso.length()
		if inc_dist < 320.0 and inc_dist > 5.0:
			var proximity = clamp(1.0 - inc_dist / 320.0, 0.0, 1.0)
			p.incense_shadow_pivot.rotation = inc_iso.angle() + PI / 2.0
			p.incense_shadow_pivot.scale = Vector2(1.0, lerp(0.55, 1.15, proximity))
			p.incense_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, proximity * 0.35))
		else:
			p.incense_shadow.material.set_shader_parameter("shadow_tint", Color(0, 0, 0, 0))

	# 2. Inter-plant shadow occlusion
	for b in active_plants:
		if not is_instance_valid(b): continue
		var cover_height: float = 0.0
		var cover_strength: float = 0.0

		for a in active_plants:
			if a == b or not is_instance_valid(a): continue

			# Sun shadow casting from A to B
			var shadow_world_len = a.visual_height * sun_length * sun_shadow_world_dir.length()
			var to_b = b.global_position - a.global_position
			var unit_dir = sun_shadow_world_dir.normalized()

			if unit_dir.length_squared() > 0 and shadow_world_len > 8.0:
				var along = to_b.dot(unit_dir)
				var perp = abs(to_b.dot(Vector2(-unit_dir.y, unit_dir.x)))
				var half_w = a.visual_width * 0.32

				if along > 8.0 and along < shadow_world_len and perp < half_w:
					var reach_ratio = clamp((1.0 - along / shadow_world_len) * (a.visual_height / max(b.visual_height, 1.0)), 0.0, 1.0)
					var lateral_fit = smoothstep(half_w, half_w * 0.25, perp)
					var strength = lateral_fit * 0.65

					cover_height = max(cover_height, reach_ratio)
					cover_strength = clamp(cover_strength + strength, 0.0, 0.70)

			# Lantern shadow casting from A to B
			var l_iso_delta = (a.global_position - mouse_pos) / Vector2(1.0, shadow_scale_y)
			var l_dist = l_iso_delta.length()
			if l_dist < lantern_radius and l_dist > 5.0:
				var l_proximity = clamp(1.0 - l_dist / lantern_radius, 0.0, 1.0)
				var l_dir = Vector2(cos(l_iso_delta.angle()), sin(l_iso_delta.angle()) * shadow_scale_y)
				var l_unit = l_dir.normalized()
				var l_len = a.visual_height * lerp(shadow_scale_y, 1.85, l_proximity) * l_dir.length()

				var l_along = to_b.dot(l_unit)
				var l_perp = abs(to_b.dot(Vector2(-l_unit.y, l_unit.x)))
				var l_half_w = a.visual_width * 0.32

				if l_along > 8.0 and l_along < l_len and l_perp < l_half_w:
					var reach_ratio = clamp((1.0 - l_along / l_len) * (a.visual_height / max(b.visual_height, 1.0)), 0.0, 1.0)
					var lateral_fit = smoothstep(l_half_w, l_half_w * 0.25, l_perp)
					var strength = lateral_fit * (pow(l_proximity, 1.15) * 0.55)

					cover_height = max(cover_height, reach_ratio)
					cover_strength = clamp(cover_strength + strength, 0.0, 0.70)

		# Apply to B's shaders (all 3 sprites)
		for sprite in [b.sprite_red, b.sprite_green, b.sprite_blue]:
			if sprite and sprite.visible and sprite.material:
				var mat = sprite.material as ShaderMaterial
				mat.set_shader_parameter("occlusion_height", cover_height)
				mat.set_shader_parameter("occlusion_darken", cover_strength)

func clear_garden():
	for p in active_plants:
		if is_instance_valid(p):
			p.queue_free()
	active_plants.clear()

func add_plant(id: String, pos: Vector2):
	var p = plant_scene.instantiate()
	p.plant_id = id
	p.global_position = pos
	y_sort.add_child(p)
	active_plants.append(p)

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

func generate_random_garden():
	clear_garden()
	for i in range(16):
		var id = available_plants[randi() % available_plants.size()]
		var x = randf_range(320, 1280)
		var y = randf_range(180, 780)
		add_plant(id, Vector2(x, y))

func generate_demo_layout():
	clear_garden()
	# Curated Zen Garden layout showcasing root anchor, 360° shadows, lantern radial shadows, and canopy occlusion
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

	$HUD/MarginContainer/VBoxContainer/BtnRandomLayout.pressed.connect(generate_random_garden)
	$HUD/MarginContainer/VBoxContainer/BtnDemoLayout.pressed.connect(generate_demo_layout)

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
