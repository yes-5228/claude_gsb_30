import { useEffect, useMemo, useState } from 'react';

import { inspectionApi } from '../../api/inspections.js';
import { metaApi } from '../../api/meta.js';
import Field from '../../components/Field.jsx';
import Modal from '../../components/Modal.jsx';
import { GradeTag, StatusTag } from '../../components/Tags.jsx';
import { useToast } from '../../components/Toast.jsx';
import { useDictionaries } from '../../hooks/useDictionaries.js';
import { calcScore, gradeOf, resultOf } from '../../utils/scoring.js';
import { toDateTimeInput } from '../../utils/format.js';

const normalizeItems = (items) =>
  items.map((item) => ({
    name: item.name,
    score: Number(item.score),
    remark: item.remark ? item.remark : null,
  }));

export default function InspectionFormModal({ inspection, defaultRestroomId, onClose, onSaved }) {
  const isEdit = Boolean(inspection);
  const { dictionaries } = useDictionaries();
  const toast = useToast();
  const [options, setOptions] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({
    restroom_id: inspection?.restroom_id ?? (defaultRestroomId ? Number(defaultRestroomId) : ''),
    inspector: inspection?.inspector ?? '',
    shift: inspection?.shift ?? '早班',
    inspect_time: inspection ? toDateTimeInput(inspection.inspect_time) : toDateTimeInput(),
    remark: inspection?.remark ?? '',
  });
  const [items, setItems] = useState(() =>
    inspection ? normalizeItems(inspection.items || []) : [],
  );

  const originalItems = useMemo(
    () => (inspection ? normalizeItems(inspection.items || []) : []),
    [inspection],
  );

  useEffect(() => {
    metaApi
      .restroomOptions()
      .then(setOptions)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (isEdit) return;
    const template = dictionaries?.inspection_check_items || [];
    setItems(template.map((name) => ({ name, score: 9, remark: '' })));
  }, [dictionaries, isEdit]);

  const score = useMemo(() => calcScore(items), [items]);
  const grade = gradeOf(score);
  const result = resultOf(items, score);

  const setItemScore = (index, value) => {
    setItems((prev) =>
      prev.map((item, idx) => (idx === index ? { ...item, score: Number(value) } : item)),
    );
  };

  const setItemRemark = (index, value) => {
    setItems((prev) => prev.map((item, idx) => (idx === index ? { ...item, remark: value } : item)));
  };

  const removeItem = (index) => {
    setItems((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== index) : prev));
  };

  // 把模板里被删掉的标准检查项补回来（默认 9 分，可再调整）
  const restoreMissingItems = () => {
    const template = dictionaries?.inspection_check_items || [];
    setItems((prev) => {
      const existing = new Set(prev.map((item) => item.name));
      const missing = template.filter((name) => !existing.has(name));
      return [...prev, ...missing.map((name) => ({ name, score: 9, remark: '' }))];
    });
  };

  const fillAll = (value) => setItems((prev) => prev.map((item) => ({ ...item, score: value })));

  const missingCount = (dictionaries?.inspection_check_items || []).filter(
    (name) => !items.some((item) => item.name === name),
  ).length;

  const submit = async (event) => {
    event.preventDefault();
    if (!form.restroom_id) {
      setError('请选择被巡查的公厕');
      return;
    }
    if (!form.inspector.trim()) {
      setError('请填写巡查人');
      return;
    }
    if (!items.length) {
      setError('至少保留一个检查项');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (isEdit) {
        const payload = {
          inspector: form.inspector,
          shift: form.shift,
          inspect_time: form.inspect_time ? new Date(form.inspect_time).toISOString() : null,
          remark: form.remark,
        };
        // 检查项有改动才提交，由服务端联动重判已登记的问题记录
        if (JSON.stringify(normalizeItems(items)) !== JSON.stringify(originalItems)) {
          payload.items = normalizeItems(items);
        }
        const updated = await inspectionApi.update(inspection.id, payload);
        const sync = updated.issue_sync;
        if (sync && (sync.adjusted || sync.voided)) {
          toast.success(
            `巡查已更新，联动问题记录：调整 ${sync.adjusted} 条、作废 ${sync.voided} 条、保留 ${sync.kept} 条`,
          );
        } else {
          toast.success('巡查记录已更新，关联问题判断不变');
        }
      } else {
        await inspectionApi.create({
          ...form,
          restroom_id: Number(form.restroom_id),
          inspect_time: form.inspect_time ? new Date(form.inspect_time).toISOString() : null,
          items,
        });
        toast.success('巡查记录已提交');
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={isEdit ? '改分 / 编辑巡查记录' : '新增保洁巡查记录'}
      onClose={onClose}
      width={880}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="submit" form="inspection-form" className="btn btn-primary" disabled={saving}>
            {saving ? '提交中…' : isEdit ? '保存修改' : '提交巡查'}
          </button>
        </>
      }
    >
      {error ? <div className="alert alert-error">{error}</div> : null}
      {isEdit && inspection.issue_count > 0 ? (
        <div className="alert alert-info">
          该巡查已登记 {inspection.issue_count} 条问题：改分或删除检查项后，待整改的问题会按新评分
          自动调整或作废，已进入整改流程的问题保留不变。
        </div>
      ) : null}
      <form id="inspection-form" onSubmit={submit} className="form-grid">
        <Field label="被巡查公厕 *">
          <select
            value={form.restroom_id}
            disabled={isEdit}
            onChange={(event) => setForm((prev) => ({ ...prev, restroom_id: event.target.value }))}
          >
            <option value="">请选择公厕</option>
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.code} {option.name}（{option.district}）
              </option>
            ))}
          </select>
        </Field>
        <Field label="巡查人 *">
          <input
            value={form.inspector}
            onChange={(event) => setForm((prev) => ({ ...prev, inspector: event.target.value }))}
            placeholder="请输入巡查人姓名"
          />
        </Field>
        <Field label="班次">
          <select
            value={form.shift}
            onChange={(event) => setForm((prev) => ({ ...prev, shift: event.target.value }))}
          >
            {(dictionaries?.shift || ['早班', '中班', '晚班']).map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </Field>
        <Field label="巡查时间">
          <input
            type="datetime-local"
            value={form.inspect_time}
            onChange={(event) => setForm((prev) => ({ ...prev, inspect_time: event.target.value }))}
          />
        </Field>
      </form>

      <div className="card-title">
        <div className="inline">
          <h3>检查项评分（每项 0-10 分）</h3>
          <span className="tag tag-primary">当前得分 {score.toFixed(1)}</span>
          <GradeTag grade={grade} />
          <StatusTag status={result} />
        </div>
        <div className="inline">
          {missingCount > 0 ? (
            <button type="button" className="btn btn-sm" onClick={restoreMissingItems}>
              补齐缺失检查项（{missingCount}）
            </button>
          ) : null}
          <button type="button" className="btn btn-sm" onClick={() => fillAll(10)}>
            全部满分
          </button>
          <button type="button" className="btn btn-sm" onClick={() => fillAll(8)}>
            全部良好
          </button>
        </div>
      </div>

      <div className="check-grid">
        {items.map((item, index) => (
          <div className={`check-item${item.score < 6 ? ' is-low' : ''}`} key={item.name}>
            <div className="name">
              {item.name}
              {items.length > 1 ? (
                <button
                  type="button"
                  className="btn-link danger"
                  style={{ marginLeft: 6, fontSize: 12 }}
                  title="从本次巡查中删除该检查项"
                  onClick={() => removeItem(index)}
                >
                  删除
                </button>
              ) : null}
            </div>
            <div className="score-line">
              <input
                type="range"
                min="0"
                max="10"
                step="1"
                value={item.score}
                onChange={(event) => setItemScore(index, event.target.value)}
              />
              <strong>{item.score}</strong>
            </div>
            <input
              style={{ marginTop: 6, fontSize: 12.5, padding: '4px 8px' }}
              className="field-input"
              placeholder="备注（可选）"
              value={item.remark || ''}
              onChange={(event) => setItemRemark(index, event.target.value)}
            />
          </div>
        ))}
      </div>

      <Field label="巡查备注" full>
        <textarea
          rows="2"
          value={form.remark}
          onChange={(event) => setForm((prev) => ({ ...prev, remark: event.target.value }))}
          placeholder="整体情况说明，发现问题可在此描述"
        />
      </Field>
    </Modal>
  );
}
