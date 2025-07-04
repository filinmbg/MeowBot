import pandas as pd

from models.xgboost_model import predict_xgboost
from models.lstm_model import predict_lstm
from models.transformer_model import predict_transformer

MODEL_MAPPING = {
    "xgboost": predict_xgboost,
    "lstm": predict_lstm,
    "transformer": predict_transformer,
}

def predict_with_model(model_type: str, model, bar: pd.DataFrame) -> str:
    if model_type not in MODEL_MAPPING:
        raise ValueError(f"Unknown model type: {model_type}")
    return MODEL_MAPPING[model_type](model, bar)
