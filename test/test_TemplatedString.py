from templated_string import TemplatedString


def test__parse_variables():
    text = "${Var1}! Hello ${Var2}!${Var3}! World ${Var4}!"

    templated = TemplatedString(text, var_token_end="}!")

    names = templated.get_variable_names()
    assert "Var1" in names
    assert "Var2" in names
    assert "Var3" in names
    assert "Var4" in names

    substitute = templated.substitute({"Var1": "Var1Value", "Var2": "Var2Value", "Var3": "Var3Value", "Var4": "Var4Value"})

    assert substitute == 'Var1Value Hello Var2ValueVar3Value World Var4Value'

