from uuid import uuid4

import pytest
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.rbac.policy import SOD_ACTIONS, assert_separation_of_duties


@pytest.mark.parametrize("action", sorted(SOD_ACTIONS))
def test_maker_cannot_approve_or_finalize(action: str) -> None:
    maker, approver, final = uuid4(), uuid4(), uuid4()
    with pytest.raises(ApiError) as caught:
        assert_separation_of_duties(maker, maker, action)
    assert caught.value.code == ErrorCode.SEPARATION_OF_DUTIES
    with pytest.raises(ApiError):
        assert_separation_of_duties(maker, approver, action, final_approver_user_id=maker)
    assert_separation_of_duties(maker, approver, action, final_approver_user_id=final)


def test_unknown_action_fails_closed() -> None:
    with pytest.raises(ValueError):
        assert_separation_of_duties(uuid4(), uuid4(), "unknown.approve")
