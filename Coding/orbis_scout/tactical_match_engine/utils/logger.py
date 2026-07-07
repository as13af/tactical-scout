import logging

# Configure root logger
logger = logging.getLogger("tactical_match_engine")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
formatter = logging.Formatter('[%(levelname)s] %(asctime)s %(name)s: %(message)s')
handler.setFormatter(formatter)

if not logger.hasHandlers():
    logger.addHandler(handler)

# Helper functions

def log_score_breakdown(scores: dict):
    logger.info(f"Score breakdown: {scores}")

def log_contender_impact(summary: dict):
    logger.info(f"Contender impact summary: {summary}")

def log_final_compatibility(score: float):
    logger.info(f"Final compatibility score: {score}")

def log_warning(message: str):
    logger.warning(message)

def log_error(message: str):
    logger.error(message)
