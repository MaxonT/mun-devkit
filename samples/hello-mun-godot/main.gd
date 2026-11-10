extends Control
## MUN Hello for Godot: the same small game as the native sample, through the
## Godot adapter. A count the player changes, a sound on each change, a save
## the console confirms, and a clean exit.
##
## Controls: Up/Down or the D-pad or left stick change the count; Enter,
## Space or the south button save; Escape or the east button quit.
##
## For automated checks, a variable the console never sets:
##   MUN_HELLO_SCRIPT="up up save wait quit"   one step a frame: up, down,
##   save, wait (for the save's answer), idle:N, quit, fail (exit code 3).

const SAVE_SCHEMA := 1
const COUNT_MAX := 9999

var count := 0
var saves := 0
var dirty := false
var saving := false
var _script_steps: PackedStringArray
var _script_idle := 0
var _stick_held := 0
var _count_label: Label
var _save_label: Label
var _facts_label: Label
var _blip: AudioStreamPlayer


func _ready() -> void:
	_build_screen()
	_blip = AudioStreamPlayer.new()
	_blip.stream = _tone(880.0, 0.07)
	add_child(_blip)
	print("hello-mun-godot: ", "in a MUN session" if Mun.in_session else "not in a MUN session",
			"; card ", Mun.card_id, ", version ", Mun.content_version, ", profile ", Mun.runtime_profile,
			", display ", Mun.display_mode, "; engine ", Engine.get_version_info()["string"],
			", video ", DisplayServer.get_name(), ", audio ", AudioServer.get_driver_name())
	_restore()
	_script_steps = OS.get_environment("MUN_HELLO_SCRIPT").replace(",", " ").split(" ", false)
	_blip.play()   # one sound at start: a silent television is a finding
	_show()


func _restore() -> void:
	var loaded := Mun.save_load()
	var status: int = loaded["status"]
	var text := "NEW GAME"
	if status == Mun.Status.OK:
		var data: Dictionary = loaded["data"]
		if loaded["schema"] == SAVE_SCHEMA and typeof(data.get("count")) == TYPE_FLOAT \
				and typeof(data.get("saves")) == TYPE_FLOAT:
			count = clampi(int(data["count"]), 0, COUNT_MAX)
			saves = maxi(int(data["saves"]), 0)
			text = "RESTORED THE PREVIOUS SAVE" if loaded["recovered"] else "RESTORED FROM THE CARD"
		else:
			text = "SAVE OF SCHEMA %d NOT UNDERSTOOD" % loaded["schema"]
	elif status == Mun.Status.UNAVAILABLE:
		text = "NOT IN A MUN SESSION"
	elif status != Mun.Status.NO_SAVE:
		text = "THE CARD'S SAVE IS DAMAGED"
	_save_label.text = text
	print("hello-mun-godot: restore: ", Mun.status_name(status), " (count ", count, ", saves ", saves, ")")


func _change(delta: int) -> void:
	var next := clampi(count + delta, 0, COUNT_MAX)
	if next == count:
		return
	count = next
	dirty = true
	_blip.play()
	_show()


func _save() -> void:
	if saving:
		return   # one save at a time: the console would answer save_busy
	saving = true
	var saved_count := count
	var data := {"count": saved_count, "saves": saves + 1}
	_save_label.text = "SAVE: WRITING..."
	print("hello-mun-godot: save requested: ", JSON.stringify(data))
	var result: Dictionary = await Mun.save_write(data, SAVE_SCHEMA)
	saving = false
	if result["status"] == Mun.Status.OK:
		saves += 1
		dirty = count != saved_count   # the player may have moved on meanwhile
		_save_label.text = "SAVED ON THE CARD (%d BYTES)" % result["bytes"]
		print("hello-mun-godot: save confirmed: ", result["bytes"], " bytes")
	else:
		var why: String = result.get("code", Mun.status_name(result["status"]))
		_save_label.text = "NOT SAVED: " + why
		print("hello-mun-godot: save not confirmed: ", Mun.status_name(result["status"]), " (",
				result.get("code", ""), ") ", result.get("message", ""))
	_show()


func _quit(code: int = 0) -> void:
	print("hello-mun-godot: exit ", code, "; count ", count, ", saves confirmed ", saves)
	get_tree().quit(code)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed:
		match event.keycode:
			KEY_UP: _change(+1)
			KEY_DOWN: _change(-1)
			KEY_ENTER, KEY_KP_ENTER, KEY_SPACE:
				if not event.echo:
					_save()
			KEY_ESCAPE: _quit()
	elif event is InputEventJoypadButton and event.pressed:
		match event.button_index:
			JOY_BUTTON_DPAD_UP: _change(+1)
			JOY_BUTTON_DPAD_DOWN: _change(-1)
			JOY_BUTTON_A, JOY_BUTTON_START: _save()   # A is the south button, whatever is printed on it
			JOY_BUTTON_B: _quit()


func _notification(what: int) -> void:
	# The window system asked the game to close: the engine's own clean way out.
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		_quit()


func _process(_delta: float) -> void:
	# The left stick behaves as a D-pad that repeats while it is held.
	var y := 0.0
	for pad in Input.get_connected_joypads():
		y = Input.get_joy_axis(pad, JOY_AXIS_LEFT_Y)
		break
	if absf(y) > 0.5:
		if _stick_held % 11 == 0:
			_change(-1 if y > 0 else +1)
		_stick_held += 1
	else:
		_stick_held = 0
	_play_script()


func _play_script() -> void:
	if _script_idle > 0:
		_script_idle -= 1
		return
	if _script_steps.is_empty():
		return
	var step := _script_steps[0]
	if step == "wait" and saving:
		return   # stay on this step until the save is answered
	_script_steps.remove_at(0)
	if step == "up":
		_change(+1)
	elif step == "down":
		_change(-1)
	elif step == "save":
		_save()
	elif step == "quit":
		_quit()
	elif step == "fail":
		_quit(3)
	elif step.begins_with("idle:"):
		_script_idle = int(step.trim_prefix("idle:"))


# ------------------------------------------------------------------ picture and sound

func _label(text: String, size: int, y: float, colour: Color) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", size)
	label.add_theme_color_override("font_color", colour)
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
	label.position.y = y
	add_child(label)
	return label


func _build_screen() -> void:
	var ink := Color("#EFE9DD")
	var accent := Color("#E39A63")
	var background := ColorRect.new()
	background.color = Color("#16202B")
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(background)
	var bar := ColorRect.new()
	bar.color = accent
	bar.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
	bar.custom_minimum_size.y = 20
	add_child(bar)
	_label("MUN HELLO (GODOT)", 72, 80, ink)
	_count_label = _label("0", 320, 250, accent)
	_save_label = _label("", 48, 700, ink)
	_label("UP/DOWN: CHANGE    ENTER OR A: SAVE    ESC OR B: QUIT", 32, 880, ink)
	_facts_label = _label("", 24, 960, ink.darkened(0.4))
	_facts_label.text = "CARD %s  VERSION %s  PROFILE %s  DISPLAY %s  GODOT %s" % [
			Mun.card_id if Mun.card_id else "-", Mun.content_version if Mun.content_version else "-",
			Mun.runtime_profile if Mun.runtime_profile else "-", Mun.display_mode if Mun.display_mode else "-",
			Engine.get_version_info()["string"]]


func _show() -> void:
	_count_label.text = "%d%s" % [count, " *" if dirty else ""]


## A short tone as a sound the engine can play: 16-bit mono at 48 kHz.
func _tone(frequency: float, seconds: float) -> AudioStreamWAV:
	var rate := 48000
	var frames := int(rate * seconds)
	var data := PackedByteArray()
	data.resize(frames * 2)
	for n in frames:
		var envelope := minf(1.0, minf(n / 240.0, (frames - n) / 960.0))
		data.encode_s16(n * 2, int(32767.0 * 0.5 * envelope * sin(TAU * frequency * n / rate)))
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = rate
	stream.data = data
	return stream
