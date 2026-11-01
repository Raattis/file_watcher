#!/usr/bin/env python3
import curses
import queue
import re
import subprocess
import threading
from collections import OrderedDict
import os

def path_segments(path, width):
	"""Return (text, style) segments that fit the path column."""
	if width <= 0:
		return []

	filename = os.path.basename(path) or path
	name_start = len(path) - len(filename)

	if len(path) <= width:
		return [(path[:name_start], "normal"), (filename, "filename")]

	if width <= 3:
		return [("." * width, "dim")]

	# The filename fits: keep it whole, shortening the surrounding path.
	if len(filename) + 3 <= width:
		head_len = max(0, (width - 3 - len(filename)) // 2)
		suffix_len = width - 3 - head_len
		suffix_start = len(path) - suffix_len
		return [
			(path[:head_len], "normal"),
			("...", "dim"),
			(path[suffix_start:name_start], "normal"),
			(filename, "filename"),
		]

	# For very narrow columns, use a simple start/.../end truncation.
	if width < 10:
		left = (width - 3 + 1) // 2
		right = width - 3 - left
		suffix_start = len(path) - right if right else len(path)
		return [
			(path[:left], "normal"),
			("...", "dim"),
			(path[suffix_start:name_start], "normal"),
			(path[max(suffix_start, name_start):], "filename"),
		]

	# The filename itself is too wide: keep its beginning and end.
	parent = os.path.dirname(path)
	room = width - 7  # two "..." markers and a slash
	head_len = max(1, room // 3)
	dir_len = min(len(parent), room // 5)
	name_room = room - head_len - dir_len
	name_head_len = (name_room + 1) // 2
	name_tail_len = name_room - name_head_len

	return [
		(path[:head_len], "normal"),
		("...", "dim"),
		(parent[-dir_len:] if dir_len else "", "normal"),
		("/" if dir_len else "", "normal"),
		(filename[:name_head_len], "filename"),
		("...", "dim"),
		(filename[-name_tail_len:] if name_tail_len else "", "filename"),
	]

# Matches lines like:
# 13:13:53.513763 processname(2335): W   /home/name/file.log
LINE_RE = re.compile(
	r"^\s*(?P<time>[^.]+)\S+\s+"
	r"(?P<process>.*?)\(\d+\):\s+"
	r"(?P<events>\S+)\s+"
	r"(?P<path>.*)$"
)

paused = False

def read_fatrace(proc, messages):
	global paused
	try:
		for line in proc.stdout:
			if paused:
				continue
			line = line.rstrip("\n")
			match = LINE_RE.match(line)
			if match:
				item = (
					match["path"],
					match["process"],
					match["events"],
					match["time"],
				)
			else:
				# Keep unexpected output visible rather than dropping it.
				item = (line, "", "", "")
			messages.put(item)
	finally:
		messages.put(None)

def main(stdscr):
	global paused
	curses.curs_set(0)
	stdscr.timeout(100)

	if curses.has_colors():
		curses.start_color()
		curses.use_default_colors()

		curses.init_pair(1, curses.COLOR_CYAN, -1)	 # filename
		curses.init_pair(2, curses.COLOR_GREEN, -1)	 # W
		curses.init_pair(3, curses.COLOR_CYAN, -1)	  # R
		curses.init_pair(4, curses.COLOR_RED, -1)		# D
		curses.init_pair(5, curses.COLOR_YELLOW, -1)	 # +
		curses.init_pair(6, curses.COLOR_MAGENTA, -1)	# O
		curses.init_pair(7, curses.COLOR_BLACK, -1)	# O

	filename_attr = curses.color_pair(1) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
	dim_attr = curses.color_pair(7) | curses.A_DIM

	event_attrs = {
		"W": curses.color_pair(2) if curses.has_colors() else curses.A_NORMAL,
		"R": curses.color_pair(3) if curses.has_colors() else curses.A_NORMAL,
		"D": curses.color_pair(4) if curses.has_colors() else curses.A_NORMAL,
		"+": curses.color_pair(5) if curses.has_colors() else curses.A_NORMAL,
		"O": curses.color_pair(6) if curses.has_colors() else curses.A_NORMAL,
	}


	messages = queue.Queue()
	# W = write events. Remove "-f", "W" to show all fatrace events.
	command = ["sudo", "-n", "fatrace", "-t", "-f", "WOD+<>"]
	#command = ["sudo", "-n", "fatrace", "-t"]

	try:
		proc = subprocess.Popen(
			command,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			bufsize=1,
		)
	except FileNotFoundError:
		stdscr.addstr(0, 0, "Could not start fatrace or sudo. Press q to quit.")
		stdscr.getch()
		return

	threading.Thread(
		target=read_fatrace, args=(proc, messages), daemon=True
	).start()

	lines = OrderedDict()
	scroll = 0
	paused = False
	error = ""

	while True:
		height, width = stdscr.getmaxyx()
		stdscr.erase()

		title = "file_watcher | (q)uit (c)lear (space)pause  (↑/↓)scroll"
		stdscr.addnstr(0, 0, title, max(0, width - 1), curses.A_BOLD)

		if error:
			stdscr.addnstr(1, 0, error, max(0, width - 1))

		while True:
			try:
				item = messages.get_nowait()
			except queue.Empty:
				break
			if item is None:
				error = "fatrace stopped. Check that it is installed and sudo access is available."
				continue
			path = item[0]
			lines[path] = item
			lines.move_to_end(path)  # newest event goes to the end
			if len(lines) > 2000:
				lines.popitem(last=False)  # discard the least recently updated file

		available_rows = max(0, height - 2)
		max_scroll = max(0, len(lines) - available_rows)
		scroll = min(scroll, max_scroll)
		visible = list(reversed(lines.values()))[scroll:scroll + available_rows]

		proc_width = 16
		event_width = 5
		time_width = 8
		inner_width = max(0, width - 1)
		metadata_width = proc_width + 1 + event_width + 1 + time_width
		path_width = max(0, inner_width - metadata_width)

		for row, (path, process, events, timestamp) in enumerate(visible, start=2):
			col = 0
			for text, style in path_segments(path, path_width):
				if not text:
					continue
				attr = filename_attr if style == "filename" else (
					dim_attr if style == "dim" else curses.A_NORMAL
				)
				stdscr.addstr(row, col, text, attr)
				col += len(text)

			# Pad the path column so the metadata stays aligned.
			if col < path_width:
				stdscr.addstr(row, col, " " * (path_width - col))
			col = path_width

			process_text = process.rjust(proc_width)[-proc_width:]
			stdscr.addstr(row, col, process_text)
			col += proc_width

			stdscr.addstr(row, col, " ")
			col += 1

			event_text = events.rjust(event_width)[-event_width:]
			for char in event_text:
				stdscr.addstr(row, col, char, event_attrs.get(char, curses.A_NORMAL))
				col += 1

			stdscr.addstr(row, col, " ")
			col += 1

			time_text = timestamp.rjust(time_width)[-time_width:]
			dot = time_text.find(".")
			if dot == -1:
				stdscr.addstr(row, col, time_text)
			else:
				stdscr.addstr(row, col, time_text[:dot])
				stdscr.addstr(row, col + dot, time_text[dot:], dim_attr)

		status = f"{len(lines)} events" + ("  PAUSED" if paused else "")
		if height > 1:
			stdscr.addnstr(height - 1, 0, status, max(0, width - 1), curses.A_DIM)

		stdscr.refresh()
		key = stdscr.getch()

		if key in (ord("q"), ord("Q")):
			break
		elif key in (ord("c"), ord("C")):
			lines.clear()
			scroll = 0
		elif key == ord(" "):
			paused = not paused
		elif key == curses.KEY_UP:
			scroll = min(scroll + 1, max_scroll)
		elif key == curses.KEY_DOWN:
			scroll = max(0, scroll - 1)
		elif key == curses.KEY_NPAGE:
			scroll = min(scroll + available_rows, max_scroll)
		elif key == curses.KEY_PPAGE:
			scroll = max(0, scroll - available_rows)

	proc.terminate()
	try:
		proc.wait(timeout=2)
	except subprocess.TimeoutExpired:
		proc.kill()

if __name__ == "__main__":
	curses.wrapper(main)
