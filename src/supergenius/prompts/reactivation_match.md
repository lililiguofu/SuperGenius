# 反向激活·匹配判断

新开放的岗位 JD 摘要与候选人曾投递另一岗位时被淘汰的简历。是否值得**以新投递**形式再次请 TA 进入初筛？

## 新开放岗位（job_id = {{new_job_id}}）JD
{{jd_excerpt}}

## 原投递岗位
{{old_job_id}}

## 简历节选
{{resume_excerpt}}

仅输出 JSON：**should_reactivate** 为 true 仅当与 JD 明显相关、值得二次邀约；**rationale** 为一句中文理由。
