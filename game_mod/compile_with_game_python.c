/* Compile one source file with the exact Python runtime embedded in The Sims 4. */

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* EA's macOS Python uses a 16-bit wchar ABI, unlike macOS system wchar_t. */
typedef uint16_t py_wchar_t;
typedef void (*py_set_path_fn)(const py_wchar_t *);
typedef void (*py_set_python_home_fn)(const py_wchar_t *);
typedef void (*py_initialize_fn)(void);
typedef int (*py_run_simple_string_flags_fn)(const char *, void *);
typedef int (*py_finalize_ex_fn)(void);


static void *required_symbol(void *library, const char *name) {
    void *symbol = dlsym(library, name);
    if (symbol == NULL) {
        fprintf(stderr, "Missing Python symbol %s: %s\n", name, dlerror());
        exit(3);
    }
    return symbol;
}


static py_wchar_t *utf8_to_python_wide(const char *input) {
    size_t length = strlen(input);
    py_wchar_t *output = calloc(length * 2 + 1, sizeof(py_wchar_t));
    if (output == NULL) {
        return NULL;
    }
    size_t source = 0;
    size_t target = 0;
    while (source < length) {
        uint32_t codepoint;
        unsigned char first = (unsigned char)input[source++];
        if (first < 0x80) {
            codepoint = first;
        } else if ((first & 0xE0) == 0xC0 && source < length) {
            uint32_t second = (uint32_t)input[source++] & 0x3F;
            codepoint = ((uint32_t)(first & 0x1F) << 6) | second;
        } else if ((first & 0xF0) == 0xE0 && source + 1 < length) {
            uint32_t second = (uint32_t)input[source++] & 0x3F;
            uint32_t third = (uint32_t)input[source++] & 0x3F;
            codepoint = ((uint32_t)(first & 0x0F) << 12) |
                (second << 6) | third;
        } else if ((first & 0xF8) == 0xF0 && source + 2 < length) {
            uint32_t second = (uint32_t)input[source++] & 0x3F;
            uint32_t third = (uint32_t)input[source++] & 0x3F;
            uint32_t fourth = (uint32_t)input[source++] & 0x3F;
            codepoint = ((uint32_t)(first & 0x07) << 18) |
                (second << 12) | (third << 6) | fourth;
        } else {
            free(output);
            return NULL;
        }

        if (codepoint <= 0xFFFF) {
            output[target++] = (py_wchar_t)codepoint;
        } else if (codepoint <= 0x10FFFF) {
            codepoint -= 0x10000;
            output[target++] = (py_wchar_t)(0xD800 + (codepoint >> 10));
            output[target++] = (py_wchar_t)(0xDC00 + (codepoint & 0x3FF));
        } else {
            free(output);
            return NULL;
        }
    }
    output[target] = 0;
    return output;
}


int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "usage: %s LIBPYTHON PYTHON_HOME SOURCE TARGET DISPLAY_PATH\n", argv[0]);
        return 2;
    }

    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (library == NULL) {
        fprintf(stderr, "Could not load game Python: %s\n", dlerror());
        return 3;
    }

    py_set_path_fn py_set_path = (py_set_path_fn)required_symbol(library, "Py_SetPath");
    py_set_python_home_fn py_set_python_home =
        (py_set_python_home_fn)required_symbol(library, "Py_SetPythonHome");
    py_initialize_fn py_initialize = (py_initialize_fn)required_symbol(library, "Py_Initialize");
    py_run_simple_string_flags_fn py_run =
        (py_run_simple_string_flags_fn)required_symbol(library, "PyRun_SimpleStringFlags");
    py_finalize_ex_fn py_finalize =
        (py_finalize_ex_fn)required_symbol(library, "Py_FinalizeEx");

    size_t path_length = strlen(argv[2]);
    char *python_path_utf8 = calloc(path_length + 16, sizeof(char));
    if (python_path_utf8 != NULL) {
        snprintf(python_path_utf8, path_length + 16, "%s/lib/python3.7", argv[2]);
    }
    py_wchar_t *python_home = utf8_to_python_wide(argv[2]);
    py_wchar_t *python_path = python_path_utf8 == NULL
        ? NULL : utf8_to_python_wide(python_path_utf8);
    if (python_home == NULL || python_path == NULL) {
        fprintf(stderr, "Could not encode Python library path\n");
        return 4;
    }

    setenv("PLUMBMATE_COMPILE_SOURCE", argv[3], 1);
    setenv("PLUMBMATE_COMPILE_TARGET", argv[4], 1);
    setenv("PLUMBMATE_COMPILE_DISPLAY", argv[5], 1);
    py_set_python_home(python_home);
    py_set_path(python_path);
    py_initialize();

    const char *script =
        "import os, py_compile\n"
        "py_compile.compile(\n"
        "    os.environ['PLUMBMATE_COMPILE_SOURCE'],\n"
        "    cfile=os.environ['PLUMBMATE_COMPILE_TARGET'],\n"
        "    dfile=os.environ['PLUMBMATE_COMPILE_DISPLAY'],\n"
        "    doraise=True,\n"
        "    optimize=0,\n"
        ")\n";
    int result = py_run(script, NULL);
    int finalize_result = py_finalize();
    free(python_path_utf8);
    free(python_home);
    free(python_path);
    dlclose(library);
    return result == 0 && finalize_result == 0 ? 0 : 5;
}
