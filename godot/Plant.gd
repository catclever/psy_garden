extends Node2D

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
var _cached_base_scale: float = 1.0
var _cached_raw_w: float = 100.0
var _cached_raw_h: float = 100.0
var _cached_base_rw: float = 20.0
var _textures_ready: bool = false

var show_footprint_ring: bool = true:
	set(value):
		if show_footprint_ring != value:
			show_footprint_ring = value
			queue_redraw()

var skip_auto_load: bool = false

@onready var sprite_red = $RedSprite
@onready var sprite_green = $GreenSprite
@onready var sprite_blue = $BlueSprite

@onready var contact_shadow = $GroundShadowPlane/ContactShadow
@onready var sun_shadow = $GroundShadowPlane/SunShadowPivot/SunShadowSprite
@onready var lantern_shadow = $GroundShadowPlane/LanternShadowPivot/LanternShadowSprite
@onready var incense_shadow = $GroundShadowPlane/IncenseShadowPivot/IncenseShadowSprite
@onready var occluder = $Occluder

@onready var sun_shadow_pivot = $GroundShadowPlane/SunShadowPivot
@onready var lantern_shadow_pivot = $GroundShadowPlane/LanternShadowPivot
@onready var incense_shadow_pivot = $GroundShadowPlane/IncenseShadowPivot

func _ready():
	if not skip_auto_load and plant_id != "":
		load_plant(plant_id)

func _draw():
	if not show_footprint_ring:
		return
	draw_set_transform(Vector2.ZERO, 0.0, Vector2(1.0, 0.55))
	draw_arc(Vector2.ZERO, fp_w * 0.5, 0.0, TAU, 36, Color(0.55, 0.92, 0.65, 0.52), 1.5, true)
	draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)

func set_perspective_scale(p_scale: float):
	perspective_scale = clampf(p_scale, 0.35, 2.5)
	if _textures_ready:
		_apply_effective_scale()

func _apply_effective_scale():
	var s: float = _cached_base_scale * clampf(perspective_scale, 0.35, 2.5)

	visual_width = _cached_raw_w * s
	visual_height = _cached_raw_h * s

	fp_w = max(10.0, _cached_base_rw * s)
	fp_h = max(6.0, fp_w * 0.55)

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

	queue_redraw()

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
	if not sprite_red: sprite_red = get_node_or_null("RedSprite")
	if not sprite_green: sprite_green = get_node_or_null("GreenSprite")
	if not sprite_blue: sprite_blue = get_node_or_null("BlueSprite")
	if not contact_shadow: contact_shadow = get_node_or_null("GroundShadowPlane/ContactShadow")
	if not sun_shadow: sun_shadow = get_node_or_null("GroundShadowPlane/SunShadowPivot/SunShadowSprite")
	if not lantern_shadow: lantern_shadow = get_node_or_null("GroundShadowPlane/LanternShadowPivot/LanternShadowSprite")
	if not incense_shadow: incense_shadow = get_node_or_null("GroundShadowPlane/IncenseShadowPivot/IncenseShadowSprite")

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
