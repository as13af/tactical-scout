
"""
Result and explanation display UI component.
"""

import customtkinter as ctk
from tkinter import messagebox
import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
import numpy as np
from tactical_match_engine.models.player_model import Player
from tactical_match_engine.models.club_model import Club
from tactical_match_engine.services.compatibility_service import CompatibilityService

class ResultView(ctk.CTkFrame):
	def __init__(self, master):
		super().__init__(master)
		self.score_label = ctk.CTkLabel(self, text="Final Compatibility Score:", font=("Arial", 18, "bold"))
		self.score_label.grid(row=0, column=0, sticky="w", pady=(0, 10))
		self.score_value = ctk.CTkLabel(self, text="-", font=("Arial", 32, "bold"))
		self.score_value.grid(row=0, column=1, sticky="w", padx=(10, 0))

		self.detail_labels = {}
		detail_names = ["Tactical Similarity", "Statistical Match", "Physical Adaptation", "Development Fit"]
		for i, name in enumerate(detail_names):
			lbl = ctk.CTkLabel(self, text=f"{name}:", font=("Arial", 14))
			lbl.grid(row=1+i, column=0, sticky="w")
			val = ctk.CTkLabel(self, text="-", font=("Arial", 14, "bold"))
			val.grid(row=1+i, column=1, sticky="w")
			self.detail_labels[name] = val

		# Radar chart
		self.radar_fig = plt.Figure(figsize=(3, 3), dpi=100)
		self.radar_ax = self.radar_fig.add_subplot(111, polar=True)
		self.radar_canvas = FigureCanvasTkAgg(self.radar_fig, master=self)
		self.radar_canvas.get_tk_widget().grid(row=0, column=2, rowspan=5, padx=30, pady=10)

		# Bar chart
		self.bar_fig = plt.Figure(figsize=(2, 2), dpi=100)
		self.bar_ax = self.bar_fig.add_subplot(111)
		self.bar_canvas = FigureCanvasTkAgg(self.bar_fig, master=self)
		self.bar_canvas.get_tk_widget().grid(row=0, column=3, rowspan=5, padx=10, pady=10)

		# Explanation box
		self.explanation_box = ctk.CTkTextbox(self, width=400, height=120, font=("Arial", 12))
		self.explanation_box.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(10, 0))
		self.explanation_box.insert("1.0", "Explanations will appear here.")
		self.explanation_box.configure(state="disabled")

		# Contender simulation summary
		self.contender_label = ctk.CTkLabel(self, text="Contender Simulation:", font=("Arial", 14, "bold"))
		self.contender_label.grid(row=6, column=0, sticky="w", pady=(10, 0))
		self.contender_box = ctk.CTkTextbox(self, width=400, height=60, font=("Arial", 12))
		self.contender_box.grid(row=7, column=0, columnspan=4, sticky="ew")
		self.contender_box.insert("1.0", "Contender simulation will appear here.")
		self.contender_box.configure(state="disabled")

	def clear(self):
		self.score_value.configure(text="-")
		for val in self.detail_labels.values():
			val.configure(text="-")
		self.radar_ax.clear()
		self.radar_canvas.draw()
		self.bar_ax.clear()
		self.bar_canvas.draw()
		self.explanation_box.configure(state="normal")
		self.explanation_box.delete("1.0", "end")
		self.explanation_box.insert("1.0", "Explanations will appear here.")
		self.explanation_box.configure(state="disabled")
		self.contender_box.configure(state="normal")
		self.contender_box.delete("1.0", "end")
		self.contender_box.insert("1.0", "Contender simulation will appear here.")
		self.contender_box.configure(state="disabled")

	def show_loading(self):
		self.score_value.configure(text="...")
		for val in self.detail_labels.values():
			val.configure(text="...")
		self.explanation_box.configure(state="normal")
		self.explanation_box.delete("1.0", "end")
		self.explanation_box.insert("1.0", "Calculating...")
		self.explanation_box.configure(state="disabled")
		self.contender_box.configure(state="normal")
		self.contender_box.delete("1.0", "end")
		self.contender_box.insert("1.0", "Calculating...")
		self.contender_box.configure(state="disabled")

	def show_error(self, message):
		messagebox.showerror("Error", message)
		self.clear()

	def calculate_and_display(self, player_id, club_id):
		# Load player and club JSON
		import os, json
		player_path = os.path.join("tactical_match_engine", "data", "players", f"{player_id}.json")
		club_path = os.path.join("tactical_match_engine", "data", "clubs", f"{club_id}.json")
		with open(player_path, "r") as f:
			player_data = json.load(f)
		with open(club_path, "r") as f:
			club_data = json.load(f)
		player = Player(player_data)
		club = Club(club_data)
		service = CompatibilityService(player, club)
		result = service.calculate_full_compatibility()
		self.display_result(result, player, club)
		return result

	def display_result(self, result, player, club):
		# Score coloring
		final_score = result["scores"]["final_score"]
		color = self._score_color(final_score)
		self.score_value.configure(text=f"{final_score*100:.1f}%", text_color=color)
		# Details
		self.detail_labels["Tactical Similarity"].configure(text=f"{result['scores']['tactical_similarity']*100:.1f}%")
		self.detail_labels["Statistical Match"].configure(text=f"{result['scores']['statistical_match']*100:.1f}%")
		self.detail_labels["Physical Adaptation"].configure(text=f"{result['scores']['physical_adaptation']*100:.1f}%")
		self.detail_labels["Development Fit"].configure(text=f"{result['scores']['development_fit']*100:.1f}%")
		# Radar chart
		self._draw_radar(player, club)
		# Bar chart
		self._draw_bar(result)
		# Explanations
		self.explanation_box.configure(state="normal")
		self.explanation_box.delete("1.0", "end")
		exp = result["explanations"]
		self.explanation_box.insert("1.0", f"Why club needs player:\n{exp['why_club_needs_player']}\n\nWhy player fits club:\n{exp['why_player_fits_club']}\n\nWhy club becomes contender:\n{exp['why_club_becomes_contender']}\n\nRisk assessment:\n{exp['risk_assessment']}")
		self.explanation_box.configure(state="disabled")
		# Contender simulation
		self.contender_box.configure(state="normal")
		self.contender_box.delete("1.0", "end")
		sim = result["contender_simulation"]
		self.contender_box.insert("1.0", f"xG gain: {sim['season_xg_gain']:.2f}\nGoal gain: {sim['goal_gain']:.2f}\nPoints gain: {sim['points_gain']:.2f}\nTitle probability shift: {sim['title_probability_shift']*100:.2f}%")
		self.contender_box.configure(state="disabled")

	def _draw_radar(self, player, club):
		# Radar axes: Progression, Risk, Defensive Intensity, Verticality, Retention
		axes = ["progressive_passes_per90", "risk", "defensive_intensity", "verticality", "retention"]
		player_vec = [float(player.stats.get(ax, 0.0)) for ax in axes]
		club_vec = [float(club.tactical_profile.get(ax, 0.0)) for ax in axes]
		labels = ["Progression", "Risk", "Def. Intensity", "Verticality", "Retention"]
		angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
		player_vec += player_vec[:1]
		club_vec += club_vec[:1]
		angles += angles[:1]
		self.radar_ax.clear()
		self.radar_ax.plot(angles, player_vec, label="Player", color="#0077b6")
		self.radar_ax.plot(angles, club_vec, label="Club Demand", color="#ff8800")
		self.radar_ax.fill(angles, player_vec, alpha=0.2, color="#0077b6")
		self.radar_ax.fill(angles, club_vec, alpha=0.2, color="#ff8800")
		self.radar_ax.set_xticks(angles[:-1])
		self.radar_ax.set_xticklabels(labels)
		self.radar_ax.set_yticklabels([])
		self.radar_ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))
		self.radar_canvas.draw()

	def _draw_bar(self, result):
		labels = ["Tactical", "Statistical", "Physical", "Development"]
		values = [
			result["scores"]["tactical_similarity"],
			result["scores"]["statistical_match"],
			result["scores"]["physical_adaptation"],
			result["scores"]["development_fit"]
		]
		self.bar_ax.clear()
		bars = self.bar_ax.bar(labels, [v*100 for v in values], color=[self._score_color(v) for v in values])
		self.bar_ax.set_ylim(0, 100)
		self.bar_ax.set_ylabel("%")
		self.bar_ax.set_title("Compatibility Breakdown")
		self.bar_canvas.draw()

	def _score_color(self, score):
		if score >= 0.85:
			return "#2ecc40"  # green
		elif score >= 0.7:
			return "#ffb300"  # orange
		else:
			return "#e74c3c"  # red
