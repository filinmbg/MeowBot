import numpy as np
from sklearn.metrics import accuracy_score, f1_score

class EarlyStopping:
    def __init__(self, patience=10):
        self.patience = patience
        self.best_loss = float("inf")
        self.counter = 0

    def step(self, val_loss):
        if val_loss < self.best_loss:
            self.best_loss = val_loss
            self.counter = 0
            return False  # не зупиняти
        else:
            self.counter += 1
            return self.counter >= self.patience

def evaluate_model(model, val_loader, device):
    model.eval()
    preds, labels = [], []

    with torch.no_grad():
        for X_batch, y_batch in val_loader:
            X_batch = X_batch.to(device)
            outputs = model(X_batch)
            predicted = (outputs.squeeze() > 0.5).int().cpu().numpy()
            preds.extend(predicted)
            labels.extend(y_batch.numpy())

    acc = accuracy_score(labels, preds)
    f1 = f1_score(labels, preds)
    return acc, f1
