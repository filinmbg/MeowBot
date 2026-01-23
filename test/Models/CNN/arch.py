# test/Models/CNN/arch.py
import torch
import torch.nn as nn
import torch.nn.functional as F

class CNNCrypto(nn.Module):
    """
    Мінімальна архітектура під state_dict:
      - conv2: Conv1d(in_channels, 64, k=3, p=1)
      - bn2:   BatchNorm1d(64)
      - fc1:   Linear(64, 32)
      - fc2:   Linear(32, out_dim)
    Очікує вхід форми (B, C, L) = (batch, in_channels, lookback)
    """
    def __init__(self, in_channels: int = 61, lookback: int = 64, out_dim: int = 1):
        super().__init__()
        self.in_channels = in_channels
        self.lookback = lookback
        self.out_dim = out_dim

        # імена шарів підібрані під твої ключі у state_dict
        self.conv2 = nn.Conv1d(in_channels, 64, kernel_size=3, padding=1, bias=True)
        self.bn2   = nn.BatchNorm1d(64)
        self.fc1   = nn.Linear(64, 32, bias=True)
        self.fc2   = nn.Linear(32, out_dim, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, L). Якщо прилетить (B, L, C) — тихо транспонуємо.
        if x.dim() == 3 and x.size(1) != self.in_channels and x.size(2) == self.in_channels:
            x = x.transpose(1, 2)  # (B, L, C) -> (B, C, L)

        h = self.conv2(x)         # (B, 64, L)
        h = self.bn2(h)
        h = F.relu(h)

        # Global Average Pooling по осі L -> (B, 64)
        h = h.mean(dim=-1)

        h = F.relu(self.fc1(h))   # (B, 32)
        out = self.fc2(h)         # (B, out_dim)
        return out
