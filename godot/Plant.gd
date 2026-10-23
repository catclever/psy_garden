extends Node2D

@export var plant_id: String = "plant_0000"

var root_anchor: Vector2 = Vector2.ZERO
var root_uv_y: float = 1.0

var visual_width: float = 100.0
var visual_height: float = 100.0

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
	load_plant(plant_id)

func load_plant(id: String):
	plant_id = id
	var base_path = "res://assets/plants/" + id + "/01/"

	var tex_red = load(base_path + "layer_red.png") if ResourceLoader.exists(base_path + "layer_red.png") else null
	var tex_green = load(base_path + "layer_green.png") if ResourceLoader.exists(base_path + "layer_green.png") else null
	var tex_blue = load(base_path + "layer_blue.png") if ResourceLoader.exists(base_path + "layer_blue.png") else null

	var ref_tex = tex_green if tex_green else (tex_red if tex_red else tex_blue)
	if not ref_tex:
		push_error("Failed to load plant textures for " + id)
		return

	var images: Array = []
	for t in [tex_red, tex_green, tex_blue]:
		if t:
			var img = t.get_image()
			if img:
				images.append(img)

	var width = images[0].get_width()
	var height = images[0].get_height()

	var top_y = height
	var bottom_y = -1
	var min_x = width
	var max_x = 0

	for img in images:
		var used = img.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			top_y = min(top_y, used.position.y)
			min_x = min(min_x, used.position.x)
			max_x = max(max_x, used.position.x + used.size.x - 1)

	for y in range(height - 1, -1, -1):
		for x in range(min_x, max_x + 1):
			for img in images:
				if img.get_pixel(x, y).a > 0.15:
					bottom_y = y
					break
			if bottom_y != -1:
				break
		if bottom_y != -1:
			break

	if bottom_y == -1:
		bottom_y = height - 1
	if top_y >= bottom_y:
		top_y = 0

	var sum_x = 0.0
	var count = 0
	var start_y = max(0, bottom_y - 7)
	for y in range(start_y, bottom_y + 1):
		for x in range(min_x, max_x + 1):
			var has_alpha = false
			for img in images:
				if img.get_pixel(x, y).a > 0.15:
					has_alpha = true
					break
			if has_alpha:
				sum_x += x
				count += 1

	var root_x = width / 2.0
	if count > 0:
		root_x = sum_x / float(count)

	root_anchor = Vector2(root_x, bottom_y)
	root_uv_y = float(bottom_y) / float(height)

	visual_width = max(24.0, float(max_x - min_x))
	visual_height = max(24.0, float(bottom_y - top_y))

	if contact_shadow:
		contact_shadow.position = Vector2.ZERO

	# Configure Foliage Sprites
	var setups = [
		{"sprite": sprite_red, "tex": tex_red},
		{"sprite": sprite_green, "tex": tex_green},
		{"sprite": sprite_blue, "tex": tex_blue}
	]

	for setup in setups:
		var spr = setup.sprite
		var tex = setup.tex
		if tex:
			spr.visible = true
			spr.texture = tex
			spr.centered = false
			spr.offset = -root_anchor
			var mat = spr.material.duplicate() as ShaderMaterial
			mat.set_shader_parameter("root_uv_y", root_uv_y)
			spr.material = mat
		else:
			spr.visible = false

	# Configure shadows
	for shadow in [sun_shadow, lantern_shadow, incense_shadow]:
		shadow.texture = ref_tex
		shadow.centered = false
		shadow.offset = -root_anchor
		var smat = shadow.material.duplicate() as ShaderMaterial
		if tex_red: smat.set_shader_parameter("tRed", tex_red)
		if tex_green: smat.set_shader_parameter("tGreen", tex_green)
		if tex_blue: smat.set_shader_parameter("tBlue", tex_blue)
		smat.set_shader_parameter("root_uv_y", root_uv_y)
		shadow.material = smat
