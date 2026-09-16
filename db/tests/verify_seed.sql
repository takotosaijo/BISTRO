-- ============================================================================
-- 冒烟测试：验证时间线关系解析、用户覆盖优先级、角色状态填充
-- 用法：psql -d bistro -v ON_ERROR_STOP=1 -f db/tests/verify_seed.sql
-- 全程在事务内执行并最终回滚，不会污染数据
-- ============================================================================

BEGIN;

INSERT INTO users (external_id, display_name) VALUES ('smoke-test', '冒烟测试');

-- 关系覆盖挂在**身份**上（2026-09-16 起），先给这个账号建一个身份
INSERT INTO personas (user_id, work_id, name, identity)
SELECT u.id, w.id, '冒烟身份', '冒烟测试用的身份'
FROM users u, works w
WHERE u.external_id = 'smoke-test' AND w.slug = 'shuihu-100';

INSERT INTO user_timeline_settings (user_id, work_id, current_anchor_id)
SELECT u.id, w.id, a.id
FROM users u, works w, timeline_anchors a
WHERE u.external_id = 'smoke-test' AND w.slug = 'shuihu-100' AND a.seq = 1;

DO $$
DECLARE
  v_user    bigint;
  v_persona bigint;
  v_work    bigint;
  v_anchor3 bigint;
  v_anchor5 bigint;
  v_anchor12 bigint;
  v_lchong  bigint;
  v_gaoqiu  bigint;
  v_wsong   bigint;
  v_pjin    bigint;
  v_label   text;
  v_src     text;
  v_loc     text;
  v_avail   character_availability;
  v_from    bigint;
BEGIN
  SELECT id INTO v_user   FROM users WHERE external_id = 'smoke-test';
  SELECT id INTO v_persona FROM personas WHERE user_id = v_user ORDER BY id LIMIT 1;
  SELECT id INTO v_work   FROM works WHERE slug = 'shuihu-100';
  SELECT id INTO v_lchong FROM characters WHERE slug = 'lin-chong';
  SELECT id INTO v_gaoqiu FROM characters WHERE slug = 'gao-qiu';
  SELECT id INTO v_wsong  FROM characters WHERE slug = 'wu-song';
  SELECT id INTO v_pjin   FROM characters WHERE slug = 'pan-jin-lian';
  SELECT id INTO v_anchor3  FROM timeline_anchors WHERE work_id = v_work AND seq = 3;
  SELECT id INTO v_anchor5  FROM timeline_anchors WHERE work_id = v_work AND seq = 5;
  SELECT id INTO v_anchor12 FROM timeline_anchors WHERE work_id = v_work AND seq = 12;

  -- 1. 锚点 1：林冲→高俅 取原著边
  SELECT label, effective_source INTO v_label, v_src
  FROM v_effective_relationships
  WHERE persona_id = v_persona AND from_id = v_lchong AND to_id = v_gaoqiu;
  IF v_label <> '殿帅府麾下的禁军教头' OR v_src <> 'canon' THEN
    RAISE EXCEPTION '锚点1 关系解析错误：label=%, source=%', v_label, v_src;
  END IF;
  RAISE NOTICE '通过 1/7  锚点1  林冲→高俅 = %（%）', v_label, v_src;

  -- 2. 推进到锚点 3：同一对关系应自动变为血仇
  UPDATE user_timeline_settings SET current_anchor_id = v_anchor3 WHERE user_id = v_user;
  SELECT label, effective_source INTO v_label, v_src
  FROM v_effective_relationships
  WHERE persona_id = v_persona AND from_id = v_lchong AND to_id = v_gaoqiu;
  IF v_label <> '不共戴天的死仇' THEN
    RAISE EXCEPTION '锚点3 关系未随时间线变化：label=%', v_label;
  END IF;
  RAISE NOTICE '通过 2/7  锚点3  林冲→高俅 随时间线变为 %', v_label;

  -- 3. 有向边独立：反向的高俅→林冲不受影响
  SELECT label, effective_source INTO v_label, v_src
  FROM v_effective_relationships
  WHERE persona_id = v_persona AND from_id = v_gaoqiu AND to_id = v_lchong;
  IF v_label <> '眼中钉，必欲除之' OR v_src <> 'canon' THEN
    RAISE EXCEPTION '反向边被错误覆盖：label=%, source=%', v_label, v_src;
  END IF;
  RAISE NOTICE '通过 3/7  有向边独立  高俅→林冲 = %', v_label;

  -- 4. 用户覆盖优先于原著
  INSERT INTO relationship_edges (
    work_id, persona_id, source, from_kind, from_id, to_kind, to_id,
    label,
    valid_from_anchor_id, override_scope
  ) VALUES (
    v_work, v_persona, 'user', 'character', v_lchong, 'character', v_gaoqiu,
    '暗中结盟', v_anchor3, 'from_here'
  );

  SELECT label, effective_source INTO v_label, v_src
  FROM v_effective_relationships
  WHERE persona_id = v_persona AND from_id = v_lchong AND to_id = v_gaoqiu;
  IF v_label <> '暗中结盟' OR v_src <> 'user' THEN
    RAISE EXCEPTION '用户覆盖未生效：label=%, source=%', v_label, v_src;
  END IF;
  RAISE NOTICE '通过 4/7  用户覆盖优先  林冲→高俅 = %（%）', v_label, v_src;

  -- 5. 状态填充：锚点 5 的武松应沿用锚点 1 的状态
  SELECT location, state_from_anchor_id INTO v_loc, v_from
  FROM v_character_states_resolved
  WHERE anchor_id = v_anchor5 AND character_id = v_wsong;
  IF v_loc <> '清河县' THEN
    RAISE EXCEPTION '状态填充失效：锚点5 武松 location=%', v_loc;
  END IF;
  RAISE NOTICE '通过 5/7  状态填充  锚点5 武松 沿用锚点1 的状态（%）', v_loc;

  -- 6. 死亡状态跨锚点延续：锚点 12 的潘金莲应仍为 deceased
  SELECT availability INTO v_avail
  FROM v_character_states_resolved
  WHERE anchor_id = v_anchor12 AND character_id = v_pjin;
  IF v_avail <> 'deceased' THEN
    RAISE EXCEPTION '死亡状态未延续：锚点12 潘金莲 availability=%', v_avail;
  END IF;
  RAISE NOTICE '通过 6/7  死亡状态延续  锚点12 潘金莲 = %', v_avail;

  -- 7. 结局锚点：武松仍在，且地点正确
  SELECT availability, location INTO v_avail, v_loc
  FROM v_character_states_resolved
  WHERE anchor_id = v_anchor12 AND character_id = v_wsong;
  IF v_avail <> 'introduced' OR v_loc <> '杭州六和寺' THEN
    RAISE EXCEPTION '结局锚点武松状态错误：availability=%, location=%', v_avail, v_loc;
  END IF;
  RAISE NOTICE '通过 7/7  结局锚点  武松 = % @ %', v_avail, v_loc;
END $$;

ROLLBACK;
