import torch
import torch.nn as nn

class CNNLSTMClassifier(nn.Module):
    def __init__(self, input_size, sequence_length):
        super(CNNLSTMClassifier, self).__init__()
        self.conv1 = nn.Conv1d(in_channels=input_size, out_channels=64, kernel_size=3)
        self.bn1 = nn.BatchNorm1d(64)
        self.lstm = nn.LSTM(input_size=64, hidden_size=128, batch_first=True)
        self.fc1 = nn.Linear(128, 64)
        self.fc2 = nn.Linear(64, 1)
        self.relu = nn.ReLU()

    def forward(self, x):
        # x: (batch, seq_len, features) → permute for Conv1D
        x = x.permute(0, 2, 1)  # (batch, features, seq_len)
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = x.permute(0, 2, 1)  # back to (batch, seq_len, channels)
        lstm_out, _ = self.lstm(x)
        x = self.relu(self.fc1(lstm_out[:, -1, :]))
        x = torch.sigmoid(self.fc2(x))
        return x
