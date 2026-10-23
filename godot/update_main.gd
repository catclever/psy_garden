extends SceneTree

func _init():
    var scene_path = "res://Main.tscn"
    var pck = ResourceLoader.load(scene_path)
    var root = pck.instantiate()

    var burner = root.get_node("IncenseBurner")

    var particles = CPUParticles2D.new()
    particles.name = "SmokeParticles"
    particles.amount = 32
    particles.lifetime = 3.0
    particles.emission_shape = CPUParticles2D.EMISSION_SHAPE_SPHERE
    particles.emission_sphere_radius = 5.0
    particles.direction = Vector2(0, -1)
    particles.spread = 15.0
    particles.gravity = Vector2(0, -20)
    particles.initial_velocity_min = 10.0
    particles.initial_velocity_max = 30.0
    particles.scale_amount_min = 2.0
    particles.scale_amount_max = 5.0
    particles.color = Color(1.0, 0.9, 0.8, 0.3)

    burner.add_child(particles)
    particles.owner = root

    var pack = PackedScene.new()
    pack.pack(root)
    ResourceSaver.save(pack, scene_path)

    print("Main.tscn updated with CPUParticles2D")
    quit()
