class TemplatedStringVariable:
    def __init__(self, name: str, start: int, end: int):
        self.name = name
        self.start = start
        self.end = end


class TemplatedString:
    def __init__(self, text, var_token_start: str = "${", var_token_end: str = "}"):
        self._text = text
        self._var_token_start = var_token_start
        self._var_token_end = var_token_end
        self._variables = {}

        self._parse_variables()

    def get_variable_names(self):
        names = []
        for name, var in self._variables.items():
            names.append(name)
        return names

    def substitute(self, value_dict: {}):
        text = self._text
        for var_name, var_value in value_dict.items():
            text = text.replace(self._var_token_start + var_name + self._var_token_end, var_value)
        return text

    def _parse_variables(self):
        var_start_index = None
        var_name = None
        sequence = ""
        for i, symbol in enumerate(self._text):
            if var_name is None:
                sequence += symbol
                if not self._var_token_start.startswith(sequence):
                    sequence = ""
                    continue

                if self._var_token_start == sequence:
                    sequence = ""
                    var_name = ""
                    var_start_index = i
                    continue

            else:
                sequence += symbol
                if not self._var_token_end.startswith(sequence):
                    var_name += symbol
                    sequence = ""
                    continue

                if self._var_token_end == sequence:
                    sequence = ""
                    var = TemplatedStringVariable(var_name, var_start_index, i)
                    self._variables[var_name] = var
                    var_name = None
