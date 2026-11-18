# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: MIT-0

extends Node
## What a MUN console gives a game session, for Godot 4.7: the session's
## facts, the save the console restored, and acknowledged saves on the Game
## Card (MUN OS docs/runtime.md and docs/saves.md).
##
## Add it as an autoload named `Mun`:
##
##     [autoload]
##     Mun="*res://addons/mun/mun.gd"
##
## It is GDScript only: the engine's own UNIX-socket stream (StreamPeerUDS)
## speaks the console's save protocol, so no native code is needed for saves.
##
## Outside a MUN session (the editor, a desktop build) every fact is empty
## and the save functions report UNAVAILABLE; nothing fails.

enum Status {
	OK,
	UNAVAILABLE, ## Not in a MUN session, or this card has no such feature.
	NO_SAVE,     ## The card holds no save for this game: a new game.
	DAMAGED,     ## A save file exists and is not a save this game can read.
	REFUSED,     ## The console answered with an error; see `code`.
	TIMEOUT,     ## No answer in time; the save may or may not be on the card.
	IO,          ## The socket failed, or the console closed it without answering.
	PROTOCOL,    ## An answer that cannot be read.
	ARGUMENT,    ## A schema below 1, or data that cannot be written as JSON.
	TOO_LARGE,   ## The payload exceeds SAVE_MAX_BYTES.
}

## The console's bound on a save's payload, measured as compact JSON in
## UTF-8, which is how this adapter writes it. (The previews before MUN OS
## v0.1.0-dev.5 counted every character outside ASCII as six bytes and could
## refuse a payload under this bound with "save_too_large".)
const SAVE_MAX_BYTES := 65536
const SAVE_FORMAT := "mun-save/1"
const _SAVE_FILE_MAX_BYTES := 112 * 1024
const _REPLY_MAX_BYTES := 4096

## True inside a MUN game session.
var in_session := false
var card_id := ""
var content_version := ""
var session := ""
var runtime_profile := ""
## The card's content, read-only; empty unless the card mounts its content.
var content_dir := ""
## "WIDTHxHEIGHT": the display's mode as the game starts, when the console says.
var display_mode := ""

var _save_file := ""
var _save_socket := ""


func _init() -> void:
	session = OS.get_environment("MUN_SESSION")
	in_session = session != ""
	card_id = OS.get_environment("MUN_CARD_ID")
	content_version = OS.get_environment("MUN_CONTENT_VERSION")
	runtime_profile = OS.get_environment("MUN_RUNTIME_PROFILE")
	content_dir = OS.get_environment("MUN_CONTENT_DIR")
	display_mode = OS.get_environment("MUN_DISPLAY_MODE")
	_save_file = OS.get_environment("MUN_SAVE_FILE")
	_save_socket = OS.get_environment("MUN_SAVE_SOCKET")


## The path of a file in the card's content, or "" when there is no content
## or `relative` is not a plain relative path (no leading "/", no "." or
## ".." segment).
func content_path(relative: String) -> String:
	if content_dir == "" or relative == "" or relative.begins_with("/") or relative.ends_with("/"):
		return ""
	for segment in relative.split("/"):
		if segment == "" or segment == "." or segment == "..":
			return ""
	return content_dir.path_join(relative)


## Reads the save the console restored for this session. Returns a
## Dictionary: `status` (a Status); and on OK, `schema` (the save schema the
## game declared), `recovered` (true when the console handed out the previous
## save because the current one was missing) and `data` (the game's own
## Dictionary, as saved). On DAMAGED, start without it and do not save over
## it unasked: the console keeps it until the player saves on purpose.
func save_load() -> Dictionary:
	if _save_file == "":
		return {"status": Status.UNAVAILABLE}
	if not FileAccess.file_exists(_save_file):
		return {"status": Status.NO_SAVE}
	var file := FileAccess.open(_save_file, FileAccess.READ)
	if file == null:
		return {"status": Status.IO}
	if file.get_length() > _SAVE_FILE_MAX_BYTES:
		return {"status": Status.DAMAGED}
	var text := file.get_as_text()
	file.close()
	var json := JSON.new()
	if json.parse(text) != OK or typeof(json.data) != TYPE_DICTIONARY:
		return {"status": Status.DAMAGED}
	var envelope: Dictionary = json.data
	var schema = envelope.get("schema")
	if envelope.get("format") != SAVE_FORMAT or typeof(envelope.get("payload")) != TYPE_DICTIONARY \
			or typeof(schema) != TYPE_FLOAT or schema < 1 or schema != floor(schema):
		return {"status": Status.DAMAGED}
	return {"status": Status.OK, "schema": int(schema), "recovered": envelope.has("recovered_from"),
			"data": envelope["payload"]}


## Asks the console to save `data` under the game's save `schema` (1 or
## more) and waits for its answer without blocking frames: call it with
## `await`. The console answers only once the card holds the save, or with
## the reason it does not, so show "saved" only after OK.
##
## Returns a Dictionary: `status`; on OK, `bytes` (what the card now holds);
## on REFUSED, `code` (the console's stable code: "no_space", "card_removed",
## "save_busy", ...) and `message` (its sentence). After TIMEOUT or IO the
## outcome is unknown until the next start reads the save back. One save at
## a time per session: a second request while one is in flight is refused by
## the console with "save_busy".
func save_write(data: Dictionary, schema: int, timeout_ms: int = 25000) -> Dictionary:
	if schema < 1:
		return {"status": Status.ARGUMENT}
	if _save_socket == "":
		return {"status": Status.UNAVAILABLE}
	var payload := JSON.stringify(data)
	if payload == "" or not payload.begins_with("{"):
		return {"status": Status.ARGUMENT}
	if payload.to_utf8_buffer().size() > SAVE_MAX_BYTES:
		return {"status": Status.TOO_LARGE}
	var request := ('{"type":"save","schema":%d,"data":%s}\n' % [schema, payload]).to_utf8_buffer()
	var deadline := Time.get_ticks_msec() + maxi(timeout_ms, 1)
	var peer := StreamPeerUDS.new()
	if peer.connect_to_host(_save_socket) != OK:
		return {"status": Status.IO}
	while true:
		peer.poll()
		var state := peer.get_status()
		if state == StreamPeerSocket.STATUS_CONNECTED:
			break
		if state != StreamPeerSocket.STATUS_CONNECTING:
			return {"status": Status.IO}
		if Time.get_ticks_msec() >= deadline:
			peer.disconnect_from_host()
			return {"status": Status.TIMEOUT}
		await get_tree().process_frame
	if peer.put_data(request) != OK:
		peer.disconnect_from_host()
		return {"status": Status.IO}
	var reply := PackedByteArray()
	while true:
		peer.poll()
		var available := peer.get_available_bytes()
		if available > 0:
			var chunk: Array = peer.get_data(available)
			if chunk[0] != OK:
				peer.disconnect_from_host()
				return {"status": Status.IO}
			reply.append_array(chunk[1])
			if reply.find(10) >= 0:
				break
			if reply.size() > _REPLY_MAX_BYTES:
				peer.disconnect_from_host()
				return {"status": Status.PROTOCOL}
		elif peer.get_status() != StreamPeerSocket.STATUS_CONNECTED:
			return {"status": Status.IO}          # closed without an answer
		elif Time.get_ticks_msec() >= deadline:
			peer.disconnect_from_host()
			return {"status": Status.TIMEOUT}
		else:
			await get_tree().process_frame
	peer.disconnect_from_host()
	var json := JSON.new()
	if json.parse(reply.slice(0, reply.find(10)).get_string_from_utf8()) != OK or typeof(json.data) != TYPE_DICTIONARY:
		return {"status": Status.PROTOCOL}
	var answer: Dictionary = json.data
	if answer.get("type") != "saved" or typeof(answer.get("ok")) != TYPE_BOOL:
		return {"status": Status.PROTOCOL}
	if answer["ok"]:
		return {"status": Status.OK, "bytes": int(answer.get("bytes", 0))}
	var error = answer.get("error")
	if typeof(error) != TYPE_DICTIONARY or typeof(error.get("code")) != TYPE_STRING or error["code"] == "":
		return {"status": Status.PROTOCOL}
	return {"status": Status.REFUSED, "code": error["code"], "message": str(error.get("message", ""))}


## A short name for a status, for logs.
func status_name(status: int) -> String:
	return Status.keys()[status].to_lower() if status >= 0 and status < Status.size() else "unknown"
