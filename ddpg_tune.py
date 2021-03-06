from gym_torcs import TorcsEnv
import numpy as np
import random
import argparse
from keras.models import model_from_json, Model
from keras.models import Sequential
from keras.layers.core import Dense, Dropout, Activation, Flatten
from keras.optimizers import Adam
import tensorflow as tf
from keras.engine.training import collect_trainable_weights
import json

from ReplayBuffer import ReplayBuffer
from ActorNetwork import ActorNetwork
from CriticNetwork import CriticNetwork
from OU import OU
import timeit

import signal
import sys
import matplotlib.pyplot as plt

OU = OU()       #Ornstein-Uhlenbeck Process

def playGame(train_indicator=1):    #1 means Train, 0 means simply Run
    BUFFER_SIZE = 100000
    BATCH_SIZE = 10
    GAMMA = 0.99
    TAU = 0.001 #Target Network HyperParameters
    LRA = 0.000001 #0.0001    #Learning rate for Actor
    LRC = 0.001 #0.001     #Lerning rate for Critic

    action_dim = 3  #Steering/Acceleration/Brake
    state_dim = 29  #of sensors input

    np.random.seed(1337)

    vision = False

    EXPLORE = 10000.
    episode_count = 2000
    
    max_steps = 100000
    reward = 0
    done = False
    step = 0
    epsilon = 1
    indicator = 0

    #Tensorflow GPU optimization
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    sess = tf.Session(config=config)
    from keras import backend as K
    K.set_session(sess)

    actor = ActorNetwork(sess, state_dim, action_dim, BATCH_SIZE, TAU, LRA)
    critic = CriticNetwork(sess, state_dim, action_dim, BATCH_SIZE, TAU, LRC)
    buff = ReplayBuffer(BUFFER_SIZE)    #Create replay buffer

    # Generate a Torcs environment
    env = TorcsEnv(vision=vision, throttle=True,gear_change=False)

    #Now load the weight
    print("Now we load the weight")
    folder = "../pre_"
    try:
        actor.model.load_weights(folder+"actormodel.h5")
        critic.model.load_weights(folder+"criticmodel.h5")
        actor.target_model.load_weights(folder+"actormodel.h5")
        critic.target_model.load_weights(folder+"criticmodel.h5")
        print("Weight load successfully")
    except:
        print("Cannot find the weight")

    x = np.zeros(episode_count)
    y_step = np.zeros(episode_count)
    y_reward = np.zeros(episode_count)
    print("TORCS Experiment Start.")
    for i in range(episode_count):
        steps = 0
        rds = 0
        x[i] = i
        print("Episode : " + str(i) + " Replay Buffer " + str(buff.count()))

        if np.mod(i, 3) == 0:
            ob = env.reset(relaunch=True)   #relaunch TORCS every 3 episode because of the memory leak error
        else:
            ob = env.reset()

        s_t = np.hstack((ob.angle, ob.track, ob.trackPos, ob.speedX, ob.speedY,  ob.speedZ, ob.wheelSpinVel/100.0, ob.rpm))
     
        total_reward = 0.
        for j in range(max_steps):
            loss = 0 
            epsilon -= 1.0 / EXPLORE
            a_t = np.zeros([1,action_dim]) # steer, accel, brake
            noise_t = np.zeros([1,action_dim])
            
            a_t_original = actor.model.predict(s_t.reshape(1, s_t.shape[0]))
            #noise_t[0][0] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][0],  0.0 , 0.60, 0.30)
            #noise_t[0][1] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][1],  0.5 , 1.00, 0.10)
            #noise_t[0][2] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][2], -0.1 , 1.00, 0.05)

            noise_t[0][0] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][0],  0 , 0, 0.01) # steer
            print("noise_t[0][0]: ", noise_t[0][0])
            noise_t[0][1] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][1],  1 , 0.6, 0.10) # accel
            #noise_t[0][2] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][2], 1.0, -0.1, 0.05) # brake

            #The following code do the stochastic brake
            if False:
                if random.random() <= 0.1:
                    print("**********************Now we apply the brake*************************")
                    noise_t[0][2] = train_indicator * max(epsilon, 0) * OU.function(a_t_original[0][2],  1 , 0.2, 0.10)

            a_t[0][0] = a_t_original[0][0] + noise_t[0][0]
            print("a_t_original[0][0]: ", a_t_original[0][0])
            a_t[0][1] = a_t_original[0][1] + noise_t[0][1]
            a_t[0][2] = a_t_original[0][2] + noise_t[0][2]

            ob, r_t, done, info = env.step(a_t[0])

            s_t1 = np.hstack((ob.angle, ob.track, ob.trackPos, ob.speedX, ob.speedY, ob.speedZ, ob.wheelSpinVel/100.0, ob.rpm))
        
            buff.add(s_t, a_t[0], r_t, s_t1, done)      #Add replay buffer
            
            #Do the batch update
            batch = buff.getBatch(BATCH_SIZE)
            states = np.asarray([e[0] for e in batch])
            actions = np.asarray([e[1] for e in batch])
            rewards = np.asarray([e[2] for e in batch])
            new_states = np.asarray([e[3] for e in batch])
            dones = np.asarray([e[4] for e in batch])
            y_t = np.asarray([e[1] for e in batch])

            target_q_values = critic.target_model.predict([new_states, actor.target_model.predict(new_states)])  
           
            for k in range(len(batch)):
                if dones[k]:
                    y_t[k] = rewards[k]
                else:
                    y_t[k] = rewards[k] + GAMMA*target_q_values[k]
       
            if (train_indicator):
                loss += critic.model.train_on_batch([states,actions], y_t) 
                a_for_grad = actor.model.predict(states)
                grads = critic.gradients(states, a_for_grad)
                actor.train(states, grads)
                #actor.target_train()
                #critic.target_train()

            total_reward += r_t
            s_t = s_t1
        
            print("Episode", i, "Step", step, "Action", a_t, "Reward", r_t, "Loss", loss)
        
            step += 1
            steps += 1
            rds += r_t
            if done:
                break
        y_step[i] = steps
        y_reward[i] = rds
        if np.mod(i, 3) == 0:
            if (train_indicator):
                print("Now we save model")
                actor.model.save_weights("post_actormodel.h5", overwrite=True)
                with open("post_actormodel.json", "w") as outfile:
                    json.dump(actor.model.to_json(), outfile)

                critic.model.save_weights("post_criticmodel.h5", overwrite=True)
                with open("post_criticmodel.json", "w") as outfile:
                    json.dump(critic.model.to_json(), outfile)

        print("TOTAL REWARD @ " + str(i) +"-th Episode  : Reward " + str(total_reward))
        print("Total Step: " + str(step))
        print("")
    
    plt.figure(1)
    plt.figure(num=1, figsize=(8,6))
    plt.title('Plot 1', size=14)
    plt.xlabel('ep', size=14)
    plt.ylabel('steps', size=14)
    plt.plot(x, y_step, color='b', linestyle='--', marker='o')
    plt.savefig('plot1.png', format='png')
    z1 = np.polyfit(x, y_step, 1)
    print(z1)
    #######
    plt.figure(2)
    plt.figure(num=2, figsize=(8,6))
    plt.title('Plot 2', size=14)
    plt.xlabel('ep', size=14)
    plt.ylabel('rewards', size=14)
    plt.plot(x, y_reward, color='b', linestyle='--', marker='o')
    plt.savefig('plot2.png', format='png')
    z2 = np.polyfit(x, y_reward, 1)
    print(z2)
    
    
    env.end()  # This is for shutting down TORCS
    print("Finish.")
    

def signal_handler(signal, frame):
    print('You pressed Ctrl+C!')
    # Generate a Torcs environment
    env = TorcsEnv(vision=False, throttle=True, gear_change=False)
    env.end()
    sys.exit(0)

if __name__ == "__main__":
    # if ctrl c is pressed, close env too
    signal.signal(signal.SIGINT, signal_handler)

    playGame()
