# %%
from pathlib import Path
import pandas as pd
import numpy as np
from scipy import stats
import matplotlib.pyplot as plt
from sklearn.preprocessing import PowerTransformer
import seaborn as sns
from scipy.stats import ks_2samp
from scipy.stats import mannwhitneyu

# ---------- 0. Path ----------
BASE_DIR = Path(__file__).resolve().parent
FEAT_DIR = BASE_DIR / 'Data/clean'

# %%
# ---------- 1. Data Overview & Cleaning ----------
df = pd.read_csv(next(FEAT_DIR.glob('*.csv')))
# %%
# ---------- 1.1 Data Description ----------
df.info()
# %%
df.describe()
# %%
# ---------- 1.2 Shares Outstanding Check ----------
df[df['shares_outstanding'].isna()]
# %%
# ---------- 1.3 Missing Value Check ----------
missing_counts = df.isna().sum()
print(missing_counts)
df.isna().mean().sort_values(ascending=False)
# %%
# ---------- 1.4 Duplicate Check ----------
df.duplicated().sum()
# %%
# ---------- 1.5 Define Numerical Columns ----------
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
exclude = ['stocks_id', 'price_open', 'price_high', 'price_low', 'option_id', 'is_settlement', 'label']
numeric_cols = [c for c in numeric_cols if c not in exclude]

# %%
# ---------- 1.6 Reassign iv ----------
df.loc[df['iv'] == -1, 'iv'] = 0

# %%
# ---------- 2. Univariate ----------
# ---------- 2.1 IQR ----------
outlier_summary = {}
for col in numeric_cols:
    q1 = df[col].quantile(0.25)
    q3 = df[col].quantile(0.75)
    iqr = q3 - q1
    mask = (df[col] < q1 - 1.5 * iqr) | (df[col] > q3 + 1.5 * iqr)
    outlier_summary[col] = {
        'n_outliers': mask.sum(),
        'pct_outliers': mask.mean()
    }

pd.DataFrame(outlier_summary).T.sort_values('pct_outliers', ascending=False)

# %%
# ---------- 2.2 Z-score ----------
z = np.abs(stats.zscore(df[numeric_cols]))
threshold = 3
row_outliers = (z > threshold).any(axis=1)
df_outliers = df.loc[row_outliers]
z_df = pd.DataFrame(z, columns=numeric_cols)
print(len(df_outliers), len(df_outliers) / len(df))
(z_df > 3).mean(axis=0).sort_values(ascending=False)

# %%
# ---------- 3. EDA ----------
for col in numeric_cols:
    plt.figure(figsize=(4,2))
    plt.boxplot(df[col], vert=False)
    plt.title(col)
    plt.show()

# %%
# ---------- 3.1 Create De-skewed Dataset ----------
df_deskewed = df.copy()
df_deskewed['dte'] = np.sqrt(df_deskewed['dte'])
pt_bc = PowerTransformer(method='box-cox', standardize=False)
pt_yj = PowerTransformer(method='yeo-johnson', standardize=False)
rs_cols = ['price_strike',
           'price',
           'volume',
           'openinterest',
           'iv',
           'theta',
           'gamma',
           'vega',
           'ask',
           'bid',
           'shares_outstanding',
           'volume_stock',
           'adv',
           'notional',
           'mcap',
           'prem_pct_mcap',
           'prem_pct_adv',
           'gamma_dollar']
ers_cols = ['prem_pct_mcap',
            'prem_pct_adv',
            'ghedge_pct_mcap',
           'ghedge_pct_adv']
df_deskewed[rs_cols] = pt_yj.fit_transform(df_deskewed[rs_cols])
df_deskewed[ers_cols] = np.log1p(df_deskewed[ers_cols])

# %%
# ---------- 3.2 Re-plot ----------
for col in numeric_cols:
    plt.figure(figsize=(4,2))
    plt.boxplot(df_deskewed[col], vert=False)
    plt.title(col)
    plt.show()

# %%
# ---------- 3.3 Heatmap ----------
# ---------- 3.3.1 Spearson ----------
corr_matrix = df_deskewed[numeric_cols].corr(method = 'spearman')

high_corr = corr_matrix.abs().unstack().sort_values(ascending=False)
high_corr = high_corr[high_corr < 1]
high_corr[high_corr > 0.9].drop_duplicates()

plt.figure(figsize=(14,12))
sns.heatmap(corr_matrix, annot=False, cmap='coolwarm', center=0, linewidths=0.5)
plt.title("Spearman Correlation Heatmap")
plt.show()
high_corr

# %%
# ---------- 3.3.2 Pearson ----------
corr_matrix = df_deskewed[numeric_cols].corr(method = 'pearson')

high_corr = corr_matrix.abs().unstack().sort_values(ascending=False)
high_corr = high_corr[high_corr < 1]
high_corr[high_corr > 0.9].drop_duplicates()

plt.figure(figsize=(14,12))
sns.heatmap(corr_matrix, annot=False, cmap='coolwarm', center=0, linewidths=0.5)
plt.title("Pearson Correlation Heatmap")
plt.show()
high_corr

# %% 
# ---------- 3.4 Delete High Correlation Features (Spearman 0.95) ----------
corr = df_deskewed[numeric_cols].corr(method = 'spearman')
upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))

high_corr_pairs = [
    (col1, col2, upper.loc[col1, col2])
    for col1 in upper.columns
    for col2 in upper.index
    if abs(upper.loc[col1, col2]) > 0.95
]

print("High Correlation Pairs")
for pair in high_corr_pairs:
    print(pair)

label_corr = df_deskewed[numeric_cols + ['label']].corr(method='spearman')['label'].abs()
to_drop = set()

for col1, col2, _ in high_corr_pairs:
    corr1 = label_corr[col1]
    corr2 = label_corr[col2]
    drop = col1 if corr1 < corr2 else col2
    to_drop.add(drop)

print("Suggest Delete:", to_drop)

final_cols = [col for col in numeric_cols if col not in to_drop] + ['label']
print("Final Features:", final_cols)

# %%
# ---------- 4 Features Validation ----------
# ---------- 4.1 Kolmogorov-Smirnov Test ----------
def ks_test(df, col):
    x = df.loc[df.label==0, col]
    y = df.loc[df.label==1, col]
    return ks_2samp(x, y)

ks_results = []
for col in final_cols:
    stat, p = ks_test(df_deskewed, col)
    ks_results.append((col, stat, p))

ks_results.sort(key=lambda x: x[2])
ks_rows = [[col, f"{stat:.5f}", f"{p:.5f}"] for col, stat, p in ks_results]
pd.DataFrame(ks_rows, columns=["Feature", "KS statistic", "KS p"])

# %%
# ---------- 4.2 Mann-Whitney U Test ----------
def u_test(df, col):
    x = df.loc[df.label==0, col]
    y = df.loc[df.label==1, col]
    return mannwhitneyu(x, y, alternative='two-sided')

u_results = []
for col in final_cols:
    stat, p = u_test(df_deskewed, col)
    u_results.append((col, stat, p))

u_results.sort(key=lambda x: x[2])
u_rows = [[col, f"{stat:.5f}", f"{p:.5f}"] for col, stat, p in u_results]
pd.DataFrame(u_rows, columns=["Features", "U statistic", "U p"])

# %%
# ---------- 4.3 Pairplot ----------
selected_cols = ['volume', 'prem_pct_adv', 'ghedge_pct_adv', 'dhedge_pct_adv', 'gamma', 'label']
sns.pairplot(df_deskewed[selected_cols], hue='label', plot_kws={'alpha':0.6})
plt.suptitle("Feature Relations Colored by Label", y=1.02)
plt.show()


# %%
# ---------- 4.4 KDE Plot ----------
for col in list(filter(lambda x: x != 'label', final_cols)):
    plt.figure(figsize=(5,4))
    sns.kdeplot(data=df_deskewed, x=col, hue='label', common_norm=False)
    plt.title(f"KDE of {col} by Label")
    plt.show()

# %%
# ---------- 5 Feature Saving ----------
RESULT_DIR = BASE_DIR / "Data/feature"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

results = df[final_cols]
results_deskewed = df_deskewed[final_cols]

results.to_csv(RESULT_DIR / "features.csv", index=False)
results_deskewed.to_csv(RESULT_DIR / "features_deskewed.csv", index=False)
# %%
