extends Node2D

@export var plant_id: String = "plant_0000"

var root_anchor: Vector2 = Vector2.ZERO
var root_uv_y: float = 1.0

var visual_width: float = 100.0
var visual_height: float = 100.0

@onready var sprite_red = $RedSprite
@onready var sprite_green = $GreenSprite
@onready var sprite_blue = $BlueSprite

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

    var tex_red = load(base_path + "layer_red.png")
    var tex_green = load(base_path + "layer_green.png")
    var tex_blue = load(base_path + "layer_blue.png")

    if not tex_red:
        push_error("Failed to load plant textures for " + id)
        return

    var image_red = tex_red.get_image()
    var width = image_red.get_width()
    var height = image_red.get_height()

    var bottom_y = -1
    for y in range(height - 1, -1, -1):
        for x in range(width):
            var c = image_red.get_pixel(x, y)
            if c.a > 0.15:
                bottom_y = y
                break
        if bottom_y != -1:
            break

    if bottom_y == -1:
        bottom_y = height - 1

    var sum_x = 0.0
    var count = 0
    var start_y = max(0, bottom_y - 7)
    for y in range(start_y, bottom_y + 1):
        for x in range(width):
            var c = image_red.get_pixel(x, y)
            if c.a > 0.15:
                sum_x += x
                count += 1

    var root_x = width / 2.0
    if count > 0:
        root_x = sum_x / count

    root_anchor = Vector2(root_x, bottom_y)
    root_uv_y = float(bottom_y) / float(height)

    visual_width = width
    visual_height = bottom_y

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
        shadow.texture = tex_red # bounds reference
        shadow.centered = false
        shadow.offset = -root_anchor
        var smat = shadow.material.duplicate() as ShaderMaterial
        smat.set_shader_parameter("tRed", tex_red)
        if tex_green: smat.set_shader_parameter("tGreen", tex_green)
        if tex_blue: smat.set_shader_parameter("tBlue", tex_blue)
        smat.set_shader_parameter("root_uv_y", root_uv_y)
        shadow.material = smat

    var poly = OccluderPolygon2D.new()
    poly.polygon = PackedVector2Array([
        Vector2(-10, 0), Vector2(10, 0), Vector2(10, -5), Vector2(-10, -5)
    ])
    occluder.occluder = poly
