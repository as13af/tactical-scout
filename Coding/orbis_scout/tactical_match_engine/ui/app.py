
"""
Main application UI using CustomTkinter.
"""

import customtkinter as ctk
from .player_view import PlayerView
from .club_view import ClubView
from .result_view import ResultView

class MainApp(ctk.CTk):
	def __init__(self):
		super().__init__()
		self.title("Tactical Compatibility Player")
		self.geometry("1100x700")
		self.resizable(False, False)
		ctk.set_appearance_mode("System")
		ctk.set_default_color_theme("blue")

		# Top section
		self.player_view = PlayerView(self, self.on_player_selected)
		self.player_view.grid(row=0, column=0, padx=20, pady=10, sticky="nw")
		self.club_view = ClubView(self, self.on_club_selected)
		self.club_view.grid(row=0, column=1, padx=20, pady=10, sticky="nw")
		self.compute_btn = ctk.CTkButton(self, text="Compute Compatibility", command=self.on_compute, width=200)
		self.compute_btn.grid(row=0, column=2, padx=20, pady=10, sticky="nw")

		# Middle and right section
		self.result_view = ResultView(self)
		self.result_view.grid(row=1, column=0, columnspan=3, padx=20, pady=10, sticky="nsew")

		self.selected_player = None
		self.selected_club = None

		self.grid_rowconfigure(1, weight=1)
		self.grid_columnconfigure(2, weight=1)

	def on_player_selected(self, player_id):
		self.selected_player = player_id

	def on_club_selected(self, club_id):
		self.selected_club = club_id

	def on_compute(self):
		self.compute_btn.configure(state="disabled")
		self.result_view.clear()
		try:
			if not self.selected_player or not self.selected_club:
				raise ValueError("Please select both a player and a club.")
			self.result_view.show_loading()
			self.after(100, self._run_compatibility)
		except Exception as e:
			self.result_view.show_error(str(e))
			self.compute_btn.configure(state="normal")

	def _run_compatibility(self):
		try:
			self.result_view.calculate_and_display(self.selected_player, self.selected_club)
		except Exception as e:
			self.result_view.show_error(str(e))
		self.compute_btn.configure(state="normal")
