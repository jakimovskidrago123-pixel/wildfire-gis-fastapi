import pickle, os

def save_model(student_username, model, folder="."):
    if model is not None:
        try:
            fn = os.path.join(folder, student_username + '.sav')
            pickle.dump(model, open(fn, 'wb'))
        except Exception as e:
            print(f'{e}: Could not save model to {fn}. Please check the folder path.')
    else:
        print('Model not found. Please make sure to fit it first. See "https://scikit-learn.org/stable/model_persistence.html" for more information.')


def run_model(student_username, test_data, test_labels, model_folder="."):
    print(f'Attempting to load from {model_folder}...')

    # try to load pkl
    fnp = os.path.join(model_folder, student_username + '.sav')
    if os.path.exists(fnp):
        try:
            model = pickle.load(open(fnp, 'rb'))
            print(f"Loaded {fnp} model.")
        except ValueError:
            print(f"I couldn't load the pickled model {fnp}. Please check it is saved correctly.")
            return 0
    else:
        print(f"Could not find model path {fnp}. Check the model exists and this is the correct filepath.")
        return 0
    try:
        score = model.score(test_data, test_labels)
        print(f"Model score: {score}")
        return score
    except:
        print("Could not score model. Attempting predictions...")
        try:
            model.predict(test_data)
            print("Could predict with model.")
        except:
            print("Model failed to predict.")
            return 0