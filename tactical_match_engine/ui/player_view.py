
"""
Player selection and display UI component.
"""

import customtkinter as ctk
from tactical_match_engine.services.compatibility_service import get_available_players

class PlayerView(ctk.CTkFrame):
	def __init__(self, master, on_select_callback):
		super().__init__(master)
		self.on_select_callback = on_select_callback
		self.label = ctk.CTkLabel(self, text="Select Player:", font=("Arial", 14, "bold"))
		self.label.pack(anchor="w", pady=(0, 5))
		self.dropdown = ctk.CTkOptionMenu(self, values=[], command=self._on_select)
		self.dropdown.pack(fill="x")
		self.refresh_players()

	def refresh_players(self):
		players = get_available_players()
		self.dropdown.configure(values=players)
		if players:
			self.dropdown.set(players[0])
			self.on_select_callback(players[0])

	def _on_select(self, value):
		self.on_select_callback(value)
