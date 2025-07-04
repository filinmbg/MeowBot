import xgboost as xgb
import pandas as pd
import joblib

def load_xgboost_model(path: str):
    return joblib.load(path)

def predict_xgboost(model, bar: pd.DataFrame) -> str:
    dmatrix = xgb.DMatrix(bar)
    pred = model.predict(dmatrix)[0]
    if pred == 1:
        return "LONG"
    elif pred == -1:
        return "SHORT"
    return "HOLD"
