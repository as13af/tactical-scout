"""
Tactical similarity calculation using cosine similarity.
Implements dot product, vector norm, and cosine similarity functions.
"""
from typing import List
import math

def dot_product(vector_a: List[float], vector_b: List[float]) -> float:
	"""
	Computes the dot product of two vectors.
	Raises ValueError if vector lengths differ.
	"""
	if len(vector_a) != len(vector_b):
		raise ValueError("Vectors must be of the same length.")
	return sum(a * b for a, b in zip(vector_a, vector_b))

def vector_norm(vector: List[float]) -> float:
	"""
	Computes the Euclidean norm (L2 norm) of a vector.
	norm(A) = sqrt(sum(Ai^2))
	"""
	return math.sqrt(sum(x ** 2 for x in vector))

def cosine_similarity(vector_a: List[float], vector_b: List[float]) -> float:
	"""
	Calculates cosine similarity between two vectors.
	similarity = dot(A, B) / (norm(A) * norm(B))
	Returns a value between 0 and 1. Returns 0.0 if either vector is zero.
	Raises ValueError if vector lengths differ.
	"""
	if len(vector_a) != len(vector_b):
		raise ValueError("Vectors must be of the same length.")
	norm_a = vector_norm(vector_a)
	norm_b = vector_norm(vector_b)
	if norm_a == 0 or norm_b == 0:
		return 0.0
	similarity = dot_product(vector_a, vector_b) / (norm_a * norm_b)
	# Clamp to [0, 1] for safety
	return max(0.0, min(1.0, similarity))
